"""Monthly 24 h precipitation verification for Shenzhen (offline builder + reader).

Day D = 20:00 BJT (D-1) .. 20:00 BJT D  (12 UTC D-1 .. 12 UTC D).
Reference: Shenzhen open-data grid analysis RAIN24H at 20:00 BJT D (exact time, cached).
Forecast: locally downloaded 0.25 deg crops in ~/hist-forecast/YYYY-MM/<model>/<run>.jsonl
          (accumulation since run start, mm), nearest grid point to each 0.01 deg cell.
Windows:  d1  = 12 UTC run of D-1, lead 0-24 h   (20时起报 · 第1天)
          d1m = 00 UTC run of D-1, lead 12-36 h  (08时起报 · 12–36小时)
No interpolation in time, no gap filling: a missing run/lead or observation drops that day
for that model only.

Maps:   model total/bias maps are also stored bilinearly interpolated (total_map_smooth, bias_map_smooth)
        for display only; every score uses the nearest-point fields.

Usage:  python monthly_precip.py 2026-09              -> writes cache/monthly_precip/2026-09.json
        python monthly_precip.py 2026-09 --maps-only  -> only adds the display maps to an existing file
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json, math, os, sys, time, warnings
import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'cache' / 'monthly_precip'
FC_ROOT = Path(os.getenv('MONTHLY_FC_ROOT', '/home/yjzx/verification-platform/hist-forecast'))
APP = Path(os.getenv('VERIFICATION_APP_ROOT', '/home/yjzx/verification-platform/ec-gfs-server'))
BJT = timezone(timedelta(hours=8))
FLAT = np.arange(22.25, 23.0 + 1e-6, 0.25)
FLON = np.arange(113.5, 114.75 + 1e-6, 0.25)
MODELS = [('ec', 'ECMWF-IFS'), ('ecai', 'ECAI'), ('ecens', 'ECMWF-ENSEMBLES平均'),
          ('gfs', 'NCEP-GFS'), ('aigfs', 'GFSAI'), ('gefs', 'GEFS集合平均')]
WINDOWS = {'d1': dict(label='前一日20时起报（预报其后0—24小时）', run_hour=12, lead0=0, lead1=24),
           'd1m': dict(label='前一日08时起报（预报其后12—36小时）', run_hour=0, lead0=12, lead1=36)}
THRESHOLDS = [0.1, 10.0, 25.0, 50.0]
THRESHOLD_NAMES = {0.1: '小雨及以上 ≥0.1', 10.0: '中雨及以上 ≥10', 25.0: '大雨及以上 ≥25', 50.0: '暴雨及以上 ≥50'}
VERSION = 'monthly-precip-v1'


def city_cells():
    """Open-data 0.01 deg cells inside the Shenzhen boundary (same mask as grid verification)."""
    from case_opendata import grid
    from shapely.geometry import shape, Point
    from shapely.ops import unary_union
    g = grid()
    city = unary_union([shape(f['geometry']) for f in
                        json.loads((APP / 'data/shenzhen_streets.geojson').read_text())['features']])
    inside = np.array([city.covers(Point(float(x), float(y))) for x, y in zip(g.lon, g.lat)])
    return g.reset_index(drop=True), inside


def load_runs(month, slug):
    """{init_utc: {lead: 2-D accumulation}} from this and the previous month folder."""
    runs = {}
    first = datetime.strptime(month, '%Y-%m')
    prev = (first - timedelta(days=1)).strftime('%Y-%m')
    for folder in {prev, month}:
        for fn in sorted((FC_ROOT / folder / slug).glob('*.jsonl')):
            init = datetime.strptime(fn.stem[:10], '%Y%m%d%H').replace(tzinfo=timezone.utc)
            acc = {}
            for line in fn.read_text().splitlines():
                if line.strip():
                    j = json.loads(line)
                    a = np.asarray(j['acc_mm'], float)
                    if a.shape == (len(FLAT), len(FLON)):
                        acc[int(j['lead'])] = a
            runs.setdefault(init, {}).update(acc)
    return runs


def window_field(runs, day_end_utc, w):
    cfg = WINDOWS[w]
    run = (day_end_utc - timedelta(hours=24 + cfg['lead0'])).replace(hour=cfg['run_hour'])
    if run + timedelta(hours=cfg['lead0'] + 24) != day_end_utc:
        return None, run
    acc = runs.get(run, {})
    a1 = acc.get(cfg['lead1'])
    a0 = np.zeros_like(a1) if cfg['lead0'] == 0 and a1 is not None else acc.get(cfg['lead0'])
    if a1 is None or a0 is None:
        return None, run
    return np.clip(a1 - a0, 0, None), run


def bilinear_weights(lat, lon):
    """0.25 deg model points -> 0.01 deg cells, bilinear. Display maps only; scores keep nearest."""
    fy = np.clip((np.asarray(lat) - FLAT[0]) / 0.25, 0, len(FLAT) - 1)
    fx = np.clip((np.asarray(lon) - FLON[0]) / 0.25, 0, len(FLON) - 1)
    j0 = np.minimum(np.floor(fy).astype(int), len(FLAT) - 2)
    i0 = np.minimum(np.floor(fx).astype(int), len(FLON) - 2)
    return j0, i0, fy - j0, fx - i0


def bilinear(field, weights):
    j0, i0, ty, tx = weights
    return ((1 - ty) * (1 - tx) * field[j0, i0] + (1 - ty) * tx * field[j0, i0 + 1]
            + ty * (1 - tx) * field[j0 + 1, i0] + ty * tx * field[j0 + 1, i0 + 1])


MAP_NOTE = '分布图里模式的累计降水和偏差按双线性插值绘制，只为看图平滑；评分仍用最近格点，与平台其他降水检验一致。'


def counts(fc, ob, t):
    f = fc >= t; o = ob >= t
    return dict(H=int((f & o).sum()), M=int((~f & o).sum()), F=int((f & ~o).sum()), C=int((~f & ~o).sum()))


def scores(c):
    H, M, F, C = c['H'], c['M'], c['F'], c['C']; n = H + M + F + C
    def div(a, b): return round(a / b, 4) if b else None
    hr = (H + M) * (H + F) / n if n else 0
    return dict(TS=div(H, H + M + F), ETS=div(H - hr, H + M + F - hr), POD=div(H, H + M),
                FAR=div(F, H + F), BIAS=div(H + F, H + M))


def observation_day(day_end_utc):
    from case_opendata import observation
    frame = observation(day_end_utc.strftime('%Y-%m-%dT%H:%M:%SZ'))
    return frame[['GRIDID', 'RAIN24H']]


def build(month):
    warnings.filterwarnings('ignore')
    cells, inside = city_cells()
    lat = cells.lat.to_numpy(); lon = cells.lon.to_numpy()
    jj = np.rint((lat - FLAT[0]) / 0.25).astype(int); ii = np.rint((lon - FLON[0]) / 0.25).astype(int)
    weights = bilinear_weights(lat, lon)
    first = datetime.strptime(month, '%Y-%m').replace(tzinfo=timezone.utc)
    nxt = (first + timedelta(days=32)).replace(day=1)
    days = [first + timedelta(days=k) for k in range((nxt - first).days)]
    runs = {slug: load_runs(month, slug) for slug, _ in MODELS}

    obs_days = {}; obs_missing = []
    for d in days:
        end = d.replace(hour=12)
        try:
            o = observation_day(end).set_index('GRIDID').RAIN24H
            v = np.array(o.reindex(cells.GRIDID).to_numpy(dtype=float), dtype=float, copy=True)
            v[v < 0] = np.nan
            obs_days[d] = v
        except Exception as exc:
            obs_missing.append(dict(day=d.strftime('%Y-%m-%d'), reason=str(exc)[:120]))

    mask_city = inside
    result = dict(version=VERSION, month=month, generated=datetime.now(BJT).isoformat(),
                  day_definition='北京时间前一日20时至当日20时（24小时）', reference='深圳开放数据格点分析场 RAIN24H（当日20时，精确整点）',
                  grid=dict(lon=sorted(set(np.round(lon, 4).tolist())), lat=sorted(set(np.round(lat, 4).tolist()))),
                  thresholds=[dict(value=t, label=THRESHOLD_NAMES[t]) for t in THRESHOLDS],
                  models=[dict(id=n, slug=s) for s, n in MODELS], windows=[], obs_missing=obs_missing,
                  method='逐日24小时累计降水，模式取0.25°格点（最近邻）对应到深圳开放数据0.01°格点，仅评深圳行政区内格点；'
                         '列联表按格点×日累计后计算TS、ETS、命中率、空报率、频率偏差。缺测日不补，各模式只在自身有资料的日子评分。' + MAP_NOTE)
    gx = np.rint((lon - min(lon)) / 0.01).astype(int); gy = np.rint((lat - min(lat)) / 0.01).astype(int)
    nx, ny = int(gx.max()) + 1, int(gy.max()) + 1

    def raster(values):
        out = [None] * (nx * ny)
        for k in np.flatnonzero(mask_city & np.isfinite(values)):
            out[int(gy[k]) * nx + int(gx[k])] = round(float(values[k]), 1)
        return out

    obs_total = np.zeros(len(cells)); obs_n = np.zeros(len(cells))
    for v in obs_days.values():
        ok = np.isfinite(v); obs_total[ok] += v[ok]; obs_n[ok] += 1
    obs_total[obs_n == 0] = np.nan
    result['grid'].update(nx=nx, ny=ny)
    result['obs'] = dict(days=len(obs_days), total_map=raster(obs_total),
                         daily=[dict(day=d.strftime('%Y-%m-%d'),
                                     mean=round(float(np.nanmean(obs_days[d][mask_city])), 2) if d in obs_days else None,
                                     max=round(float(np.nanmax(obs_days[d][mask_city])), 1) if d in obs_days else None)
                                for d in days])
    result['obs']['mean_total'] = round(float(sum(x['mean'] for x in result['obs']['daily'] if x['mean'] is not None)), 1)

    for w, cfg in WINDOWS.items():
        wres = dict(id=w, label=cfg['label'], models={})
        for slug, name in MODELS:
            fc_total = np.zeros(len(cells)); ob_total = np.zeros(len(cells)); used = np.zeros(len(cells))
            fc_smooth = np.zeros(len(cells))
            agg = {t: dict(H=0, M=0, F=0, C=0) for t in THRESHOLDS}
            daily = []; missing = []
            for d in days:
                end = d.replace(hour=12)
                field, run = window_field(runs[slug], end, w)
                ob = obs_days.get(d)
                if field is None or ob is None:
                    missing.append(dict(day=d.strftime('%Y-%m-%d'), run_bjt=run.astimezone(BJT).strftime('%Y-%m-%d %H时'),
                                        reason='模式资料缺失' if field is None else '实况缺失'))
                    daily.append(dict(day=d.strftime('%Y-%m-%d'), mean=None, max=None, obs_mean=None, scores={}))
                    continue
                fc = field[jj, ii]
                ok = mask_city & np.isfinite(ob) & np.isfinite(fc)
                fc_total[ok] += fc[ok]; ob_total[ok] += ob[ok]; used[ok] += 1
                fc_smooth[ok] += bilinear(field, weights)[ok]
                day_scores = {}
                for t in THRESHOLDS:
                    c = counts(fc[ok], ob[ok], t)
                    for k in c: agg[t][k] += c[k]
                    day_scores[f'{t:g}'] = dict(c, **scores(c))
                daily.append(dict(day=d.strftime('%Y-%m-%d'), run_bjt=run.astimezone(BJT).strftime('%Y-%m-%d %H时'),
                                  mean=round(float(fc[ok].mean()), 2), max=round(float(fc[ok].max()), 1),
                                  obs_mean=round(float(ob[ok].mean()), 2), scores=day_scores))
            n_days = sum(1 for x in daily if x['mean'] is not None)
            fc_total[used == 0] = np.nan; ob_total[used == 0] = np.nan; fc_smooth[used == 0] = np.nan
            wres['models'][name] = dict(
                days=n_days, missing=missing,
                mean_total=round(float(sum(x['mean'] for x in daily if x['mean'] is not None)), 1) if n_days else None,
                obs_mean_total=round(float(sum(x['obs_mean'] for x in daily if x['obs_mean'] is not None)), 1) if n_days else None,
                total_map=raster(fc_total), bias_map=raster(fc_total - ob_total),
                total_map_smooth=raster(fc_smooth), bias_map_smooth=raster(fc_smooth - ob_total),
                month_scores={f'{t:g}': dict(agg[t], **scores(agg[t])) for t in THRESHOLDS},
                daily=daily)
        result['windows'].append(wres)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'{month}.json'
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')), encoding='utf-8')
    os.replace(tmp, path)
    return path


def add_smooth_maps(month):
    """Add bilinear display maps to an existing month file. Offline: no observation fetch, scores untouched.

    The nearest-neighbour totals are recomputed from the forecast files first and must match the stored
    total_map on at least 99 % of the cells, otherwise nothing is written (wrong or incomplete source data).
    The few cells that differ are the ones where the observation was missing on some day, so that day was
    left out of the stored total; the same amount is taken off the smooth total there."""
    path = OUT / f'{month}.json'
    result = json.loads(path.read_text(encoding='utf-8'))
    g = result['grid']; nx, ny = g['nx'], g['ny']
    lat = np.repeat(min(g['lat']) + 0.01 * np.arange(ny), nx); lon = np.tile(min(g['lon']) + 0.01 * np.arange(nx), ny)
    jj = np.clip(np.rint((lat - FLAT[0]) / 0.25).astype(int), 0, len(FLAT) - 1)
    ii = np.clip(np.rint((lon - FLON[0]) / 0.25).astype(int), 0, len(FLON) - 1)
    weights = bilinear_weights(lat, lon)
    slugs = {m['id']: m['slug'] for m in result['models']}
    arr = lambda values: np.array([np.nan if v is None else v for v in values], float)
    back = lambda a: [None if not np.isfinite(v) else round(float(v), 1) for v in a]
    odd_cells = 0
    for w in result['windows']:
        for name, item in w['models'].items():
            runs = load_runs(month, slugs[name])
            total = np.zeros((len(FLAT), len(FLON)))
            for x in item['daily']:
                if x['mean'] is None:
                    continue
                end = datetime.strptime(x['day'], '%Y-%m-%d').replace(hour=12, tzinfo=timezone.utc)
                field, _ = window_field(runs, end, w['id'])
                if field is None:
                    raise ValueError(f"{name} {x['day']} 缺原始预报，无法补图")
                total += field
            stored = arr(item['total_map']); bias = arr(item['bias_map']); inside = np.isfinite(stored)
            left_out = np.where(inside, total[jj, ii] - stored, 0.0)
            left_out[np.abs(left_out) <= 0.051] = 0.0  # within the 0.1 mm rounding of the stored map
            odd = int((left_out != 0).sum()); odd_cells = max(odd_cells, odd)
            if odd > 0.01 * inside.sum():
                raise ValueError(f"{w['id']} {name} 按最近格点重算与原图有 {odd} 个格点不一致，原始预报可能不对，未写入")
            smooth = np.where(inside, bilinear(total, weights) - left_out, np.nan)
            item['total_map_smooth'] = back(smooth); item['bias_map_smooth'] = back(smooth - (stored - bias))
    if MAP_NOTE not in result['method']:
        result['method'] += MAP_NOTE
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')), encoding='utf-8')
    os.replace(tmp, path)
    return path, odd_cells


def catalog():
    items = []
    for p in sorted(OUT.glob('*.json'), reverse=True):
        try:
            head = json.loads(p.read_text(encoding='utf-8'))
            if head.get('version') == VERSION:
                items.append(dict(month=head['month'], generated=head['generated'], obs_days=head['obs']['days']))
        except Exception:
            continue
    return dict(months=items)


def read(month):
    if not (isinstance(month, str) and len(month) == 7 and month[4] == '-' and month.replace('-', '').isdigit()):
        raise ValueError('月份格式应为 YYYY-MM。')
    p = OUT / f'{month}.json'
    if not p.is_file():
        raise ValueError('该月份暂无月降水检验结果。')
    return p.read_bytes()


if __name__ == '__main__':
    t0 = time.time()
    if '--maps-only' in sys.argv[2:]:
        done, odd_cells = add_smooth_maps(sys.argv[1])
        print(done, f'最近格点重算与原图不一致的格点最多 {odd_cells} 个（实况缺测日）', f'{time.time() - t0:.0f}s')
    else:
        print(build(sys.argv[1]), f'{time.time() - t0:.0f}s')
