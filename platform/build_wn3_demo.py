"""Build one real, paired WN3 sample from validated local caches; no downloads."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd
from shapely.geometry import Point, shape
from shapely.ops import unary_union

sys.path.append('/home/chengyang/verification-test')
import native_archive as native
from model_interfaces import GridRequest, validate_field

APP = Path('/home/chengyang/ec-gfs-server')
WEATHER = Path('/home/chengyang/gods-eye-weather/weather-data/region-v4/weathernext3')
BJT = timezone(timedelta(hours=8))
SOURCE = 'Google DeepMind WeatherNext 3 集合均值 · 已验证本机原生缓存 · 仅用于接口演示'


def atomically_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.wn3-demo-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(destination):
    now = datetime.now(timezone.utc)
    candidates = []
    for path in sorted(WEATHER.glob('run_??????????.json'), reverse=True):
        try:
            run, directory, committed = native.wn3_manifest(path)
            lead = 6
            valid = run + timedelta(hours=lead)
            if valid > now - timedelta(hours=1) or not {4, 5, 6}.issubset(committed):
                continue
            name = valid.astimezone(BJT).strftime('%Y%m%d%H') + '.pkl'
            roots = (APP/'cache/OBSERVATION/grid', Path('/home/chengyang/verification-test/cache/obs-backfill/grid'))
            obs_path = next((root/name for root in roots if (root/name).is_file()), None)
            if obs_path is None:
                continue
            frame, issues = native.load_wn3_frame(WEATHER, run, lead, directory, committed)
            if issues or frame.get('layers', {}).get('surface', {}).get('t') is None or frame['rain3h'] is None:
                continue
            observation = pd.read_pickle(obs_path)
            if not {'GRIDID', 'T', 'RAIN03H'} <= set(observation.columns):
                continue
            candidates.append((run, lead, frame, observation, path, obs_path, directory, committed))
            break
        except native.WN3_ERRORS:
            continue
    if not candidates:
        raise RuntimeError('没有找到气温、完整3小时降水及实况共同就绪的 WN3 +6h 批次。')
    run, lead, frame, observation, manifest_path, obs_path, directory, committed = candidates[0]
    grid = pd.read_excel(APP/'data/grid.xlsx', sheet_name='数据集1').rename(columns={
        '格网ID（唯一）': 'GRIDID', '格网左下角经度（度）': 'west', '格网右上角经度（度）': 'east',
        '格网左下角纬度（度）': 'south', '格网右上角纬度（度）': 'north'})
    for column in ('GRIDID', 'west', 'east', 'south', 'north'):
        grid[column] = pd.to_numeric(grid[column], errors='coerce')
    grid = grid.dropna(subset=['GRIDID', 'west', 'east', 'south', 'north'])
    grid['GRIDID'] = grid['GRIDID'].astype(int)
    total = grid['GRIDID'].nunique()
    if observation['GRIDID'].nunique() < np.ceil(total*.95):
        raise RuntimeError('该批观测覆盖不足 95%。')
    grid['lon'] = (grid.west+grid.east)/2
    grid['lat'] = (grid.south+grid.north)/2
    city = unary_union([shape(f['geometry']) for f in json.loads((APP/'data/shenzhen_streets.geojson').read_text())['features']])
    grid = grid.loc[[city.covers(Point(float(x), float(y))) for x, y in zip(grid.lon, grid.lat)]].copy()
    valid = run + timedelta(hours=lead)
    sample_id = run.strftime('%Y%m%d%H') + f'_f{lead:03d}'
    folder = destination/sample_id
    fields, pairs, hashes = [], {}, {}
    for variable, kind, values, unit, accumulation in (
            ('temperature_2m', '气温', frame['layers']['surface']['t'], 'degC', None),
            ('precipitation', '降水', frame['rain3h'], 'mm', 180)):
        array = np.asarray(values, dtype=float).reshape(len(frame['lat']), len(frame['lon']))
        payload = dict(product_id='cma-gd-ec-mt-500m', run_utc=run.isoformat(), valid_utc=valid.isoformat(),
                       variable=variable, units=unit, level_hpa=None, source=SOURCE,
                       lon=np.asarray(frame['lon']).tolist(), lat=np.asarray(frame['lat']).tolist(), values=array.tolist(),
                       accumulation_minutes=accumulation,
                       interval_start_utc=(valid-timedelta(minutes=accumulation)).isoformat() if accumulation else None)
        request = GridRequest(payload['product_id'], payload['run_utc'], lead*60, variable, accumulation)
        validate_field(payload, request)
        payload['product_id'] = 'wn3-demo-source'
        filename = variable+'.json'
        atomically_write(folder/filename, payload)
        hashes[variable] = hashlib.sha256((folder/filename).read_bytes()).hexdigest()
        forecast, observed = native.paired(frame, array, grid, observation, kind)
        if not len(forecast):
            raise RuntimeError('演示缺少有效预报与实况配对。')
        pairs[variable] = {'forecast': forecast.tolist(), 'observed': observed.tolist()}
        fields.append(dict(variable=variable, label='2米气温' if variable=='temperature_2m' else '3小时累计降水',
                           units=unit, accumulation_minutes=accumulation, paired_points=len(forecast)))
    scored = native.score_frame(frame, grid, observation, now.timestamp())
    temperature = next(r for r in scored if r['dataset']=='hk-气温-2m')
    scores = {'temperature_2m': {k: temperature[k] for k in ('n', 'ME', 'MAE', 'RMSE', 'CC')},
              'precipitation': []}
    for row in scored:
        if row['dataset']!='hk-降水-3':
            continue
        h, m, f = row['H'], row['M'], row['F']
        scores['precipitation'].append({k: row[k] for k in ('n', 'threshold', 'H', 'M', 'F')} | {
            'TS': h/(h+m+f) if h+m+f else None,
            'POD': h/(h+m) if h+m else None, 'FAR': f/(h+f) if h+f else None})
    atomically_write(folder/'pairs.json', pairs)
    hashes['pairs'] = hashlib.sha256((folder/'pairs.json').read_bytes()).hexdigest()
    sample = dict(schema=1, is_demo=True, source_model=native.WN3_MODEL, excluded_from_official_scores=True,
                  sample_id=sample_id, run_utc=run.isoformat(), run_bjt=run.astimezone(BJT).isoformat(),
                  valid_utc=valid.isoformat(), valid_bjt=valid.astimezone(BJT).isoformat(), lead_minutes=lead*60,
                  grid_degrees=.1, interval_minutes=60,
                  bounds=[float(frame['lon'][0]), float(frame['lat'][0]), float(frame['lon'][-1]), float(frame['lat'][-1])],
                  fields=fields, scores=scores, created_bjt=now.astimezone(BJT).isoformat(), file_sha256=hashes)
    atomically_write(folder/'manifest.json', sample)
    atomically_write(destination/'current.json', sample)
    atomically_write(folder/'provenance.json', {'manifest': str(manifest_path), 'observation': str(obs_path),
                                               'source_signature': native.wn3_source_signature(native.wn3_paths(WEATHER, run, lead, directory, committed), obs_path, [])})
    print(json.dumps({k: sample[k] for k in ('sample_id', 'run_bjt', 'valid_bjt', 'grid_degrees', 'fields', 'scores')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--destination', required=True, type=Path)
    main(parser.parse_args().destination)
