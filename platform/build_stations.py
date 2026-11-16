"""Freeze the scoring-box station table from cached authoritative metadata.

No network access. CHM stations form the baseline; observed block 57/58/59
stations are joined to other GHCNh China IDs when necessary (e.g. CHU59493).
Stations without a single observation in the last ACTIVE_DAYS days of the
observation archive are moved to `inactive` (still fetched from IEM/GHCNh so
they return automatically, but not scored or drawn). With fewer than
MIN_ARCHIVE_DAYS archive days nothing is pruned.
"""
import argparse
import gzip
import re
import xml.etree.ElementTree as ET
from station_common import BOX, ROOT, XINJIANG, atomic_json, cache_root, in_box, read_json

ACTIVE_DAYS = 45
MIN_ARCHIVE_DAYS = 20
OVERRIDES = {
    "59493": dict(name="深圳国家基本站", lon=114.0, lat=round(22 + 32 / 60, 4),
                  elevation=63.0, coordinate_source="深圳国家基本气象站 (竹子林)"),
    "ZGSZ": dict(name="深圳宝安机场"),
}


def active_ids(obs, days=ACTIVE_DAYS, minimum=MIN_ARCHIVE_DAYS):
    """Station ids with any archived observation in the newest `days` daily files (None = too little archive)."""
    files = sorted(obs.glob('????-??-??.jsonl.gz')) if obs.is_dir() else []
    if len(files) < minimum:
        return None
    ids = set()
    for path in files[-days:]:
        with gzip.open(path, 'rt') as handle:
            ids.update(re.findall(r'"station":"([^"]+)"', handle.read()))
    return ids


def candidates(raw, box_only=True):
    result = {}
    for line in (raw / 'ghcnh-station-list.txt').read_text().splitlines():
        ident = line[:11]
        # GHCNh files Hong Kong/Macau under HKU.../MCU...; their CHU twins carry stale sites (45007 = Kai Tak).
        local = re.fullmatch(r'(?:HK|MC)U0(\d{5})-\d', ident)
        if not local and (not ident.startswith('CH') or not ident[-5:].isdigit()):
            continue
        lat, lon, elevation = float(line[12:20]), float(line[21:30]), float(line[31:37])
        if box_only and not in_box(lon, lat):
            continue
        wmo = local[1] if local else ident[-5:]
        station = dict(id=wmo, wmo=wmo, name=line[41:71].strip(), lat=lat, lon=lon,
                       elevation=elevation if elevation > -900 else None,
                       type='synop', ghcnh_id=ident, sources=[],
                       coordinate_source='GHCNh station list', model_elevations={})
        if wmo in OVERRIDES:
            station.update(OVERRIDES[wmo])
        if wmo not in result or ident.startswith('CHM') or local:
            result[wmo] = station
    return result


def build(raw=None, output=None, obs=None):
    obs = obs if obs is not None else (cache_root() / 'obs' if raw is None else raw.parent / 'obs')
    raw = raw or cache_root() / 'raw'
    available = {item.text.split('GHCNh_')[-1].split('_2026')[0]
                 for item in ET.fromstring((raw / 'ghcnh-inventory.xml').read_bytes()).findall('.//{*}Key')}
    all_metadata = candidates(raw, box_only=False)
    all_stations = {k:v for k,v in all_metadata.items() if in_box(v['lon'],v['lat'])}
    observed = set()
    for path in sorted((raw / 'ogimet').glob('*.txt')):
        observed.update(re.findall(r'^(\d{5}),', path.read_text(), re.M))
    stations = []
    for ident, station in sorted(all_stations.items()):
        if not station['ghcnh_id'].startswith('CHM') and ident not in observed:
            continue
        if station['ghcnh_id'] in available:
            station['sources'].append('ghcnh')
        if ident in OVERRIDES:
            station['sources'].append('szopen')
        if ident in observed:
            station['sources'].append('ogimet')
        stations.append(station)
    for feature in read_json(raw / 'CN__ASOS.geojson')['features']:
        lon, lat = feature['geometry']['coordinates'][:2]
        if not in_box(lon, lat):
            continue
        prop = feature['properties']; ident = prop['sid']
        stations.append(dict(id=ident, wmo=None, name=OVERRIDES.get(ident, {}).get('name', prop['sname']), lon=lon, lat=lat,
                             elevation=prop.get('elevation'), type='metar', sources=['iem'],
                             coordinate_source='IEM CN__ASOS', model_elevations={}))
    for ident, item in XINJIANG.items():
        stations.append(dict(id=ident, wmo=ident, type='synop', coordinate_source='GHCNh station list', model_elevations={},
                             sources=[*(['ghcnh'] if item['ghcnh_id'] in available else []), *(['ogimet'] if ident in observed else [])], **item))
    names = read_json(ROOT / 'station_names_zh.json', {})
    for station in stations:
        if station['id'] not in OVERRIDES and station['id'] in names:
            station['name'] = names[station['id']]
    active = active_ids(obs)
    inactive = [s for s in stations if active is not None and s['id'] not in active]
    stations = [s for s in stations if active is None or s['id'] in active]
    value = dict(schema=1, box=list(BOX), stations=stations, inactive=inactive,
                 active_rule=f'observation in the last {ACTIVE_DAYS} archive days' if active is not None else 'not applied',
                 baseline_chm=sum(s['type'] == 'synop' and s['ghcnh_id'].startswith('CHM') for s in stations),
                 unknown_ogimet_ids=sorted(observed - set(all_metadata)))
    import nmic_obs
    nmic_obs.add_stations(value, raw.parent)
    atomic_json(output or ROOT / 'stations.json', value)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=str)
    args = parser.parse_args()
    from pathlib import Path
    result = build(output=Path(args.output) if args.output else None)
    print('Frozen stations:', len(result['stations']))
