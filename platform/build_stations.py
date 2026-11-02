"""Freeze the scoring-box station table from cached authoritative metadata.

No network access. CHM stations form the baseline; observed block 57/58/59
stations are joined to other GHCNh China IDs when necessary (e.g. CHU59493).
"""
import argparse
import re
import xml.etree.ElementTree as ET
from station_common import BOX, ROOT, atomic_json, cache_root, in_box, read_json


def candidates(raw, box_only=True):
    result = {}
    for line in (raw / 'ghcnh-station-list.txt').read_text().splitlines():
        ident = line[:11]
        if not ident.startswith('CH') or not ident[-5:].isdigit():
            continue
        lat, lon, elevation = float(line[12:20]), float(line[21:30]), float(line[31:37])
        if box_only and not in_box(lon, lat):
            continue
        wmo = ident[-5:]
        station = dict(id=wmo, wmo=wmo, name=line[41:71].strip(), lat=lat, lon=lon,
                       elevation=elevation if elevation > -900 else None,
                       type='synop', ghcnh_id=ident, sources=[],
                       coordinate_source='GHCNh station list', model_elevations={})
        if wmo not in result or ident.startswith('CHM'):
            result[wmo] = station
    return result


def build(raw=None, output=None):
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
        if ident in observed:
            station['sources'].append('ogimet')
        stations.append(station)
    for feature in read_json(raw / 'CN__ASOS.geojson')['features']:
        lon, lat = feature['geometry']['coordinates'][:2]
        if not in_box(lon, lat):
            continue
        prop = feature['properties']; ident = prop['sid']
        stations.append(dict(id=ident, wmo=None, name=prop['sname'], lon=lon, lat=lat,
                             elevation=prop.get('elevation'), type='metar', sources=['iem'],
                             coordinate_source='IEM CN__ASOS', model_elevations={}))
    value = dict(schema=1, box=list(BOX), stations=stations,
                 baseline_chm=sum(s['type'] == 'synop' and s['ghcnh_id'].startswith('CHM') for s in stations),
                 unknown_ogimet_ids=sorted(observed - set(all_metadata)))
    atomic_json(output or ROOT / 'stations.json', value)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=str)
    args = parser.parse_args()
    from pathlib import Path
    result = build(output=Path(args.output) if args.output else None)
    print('Frozen stations:', len(result['stations']))
