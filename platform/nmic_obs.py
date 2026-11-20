"""Local SURF_CHN_MUL_HOR imports. No network or credentials are used here."""
from datetime import datetime, timedelta
from pathlib import Path
import os
import re

from station_common import ROOT, UTC, PAIR_DAYS, cache_root, finite, in_cmagd, iso, read_json
from sz_district_obs import _dewpoint

FIELDS = {'Station_Id_C', 'Year', 'Mon', 'Day', 'Hour', 'TEM', 'PRS_Sea', 'WIN_S_Avg_2mi', 'RHU', 'PRE_3h'}


def data_dir(root=None):
    return Path(os.environ.get('STATIONS_NMIC_DIR', (root or cache_root()) / 'raw/nmic'))


def number(text):
    value = finite(text)
    # Reserved characteristic codes are never physical measurements.
    return value if value is not None and abs(value) < 990000 else None


def read_records(folder, windows=None, end=None, compact=False):
    """Deduplicate whole reports; last filename/line wins, independent of mtimes.

    The export's readme contains no timezone/special-value definitions. UTC is
    supported by the independently checked SYNOP overlap. GB/T 37301-2019 E.1
    identifies 999990 as trace; all other reserved codes remain missing.
    """
    records = {}; diagnostics = dict(files=0, records=0, duplicates=0, conflicts=0)
    for path in sorted(Path(folder).glob('*.txt')):
        with path.open(encoding='utf-8-sig') as handle:
            header = next(handle, '').split()
            if not FIELDS <= set(header):
                if header and header[0] == 'Station_Id_C':
                    raise ValueError(f'Incomplete NMIC header: {path.name}')
                continue  # official readme can live beside the exports
            diagnostics['files'] += 1
            for line_no, line in enumerate(handle, 2):
                if not line.strip():
                    continue
                cells = line.split()
                if len(cells) != len(header):
                    raise ValueError(f'Invalid NMIC row: {path.name}:{line_no}')
                item = dict(zip(header, cells)); sid = item['Station_Id_C']
                at = datetime(*(int(item[k]) for k in ('Year', 'Mon', 'Day', 'Hour')), tzinfo=UTC)
                if not re.fullmatch(r'\d{5}', sid) or at.hour % 3:
                    raise ValueError(f'Invalid NMIC station/time: {path.name}:{line_no}')
                if windows is not None and (sid not in windows or at < windows[sid] or at > end):
                    continue
                if compact:
                    item = {k: item[k] for k in ('TEM','RHU','PRS_Sea','WIN_S_Avg_2mi','PRE_3h')}
                key = sid, at
                if key in records:
                    diagnostics['duplicates'] += 1
                    diagnostics['conflicts'] += records[key] != item
                records[key] = item
                diagnostics['records'] += 1
    return records, diagnostics


def observation_rows(stations, end, cutoff, root=None, records=None):
    cutoffs = {s['id']: max(cutoff, end-timedelta(days=PAIR_DAYS)) if s.get('cmagd_extended') else cutoff
               for s in stations if not s.get('nmic_cmagd_metadata_only')}
    if records is None:
        # Keep only scoreable reports plus exact backward rain tiles. Old sites
        # retain their original cutoff; large outer raw archives cannot expand
        # the in-memory observation window beyond the 16-day pair window.
        records, _ = read_records(data_dir(root), {sid:at-timedelta(hours=24) for sid,at in cutoffs.items()}, end, compact=True)
    rows = []
    for (sid, at), item in sorted(records.items()):
        if sid not in cutoffs or not cutoffs[sid] <= at <= end:
            continue
        t, rh = number(item['TEM']), number(item['RHU'])
        td = _dewpoint(t, rh) if t is not None and -100 <= t <= 70 else None
        values = dict(t=t, msl=number(item['PRS_Sea']), wind=number(item['WIN_S_Avg_2mi']), td=td)
        traces = {}
        for hours in (6, 12, 24):
            parts = [records.get((sid, at-timedelta(hours=h))) for h in range(0, hours, 3)]
            amounts = [finite(p['PRE_3h']) if p else None for p in parts]
            if all(a == 999990 or (a is not None and 0 <= a < 990000) for a in amounts):
                var = 'rain'+str(hours)
                values[var] = round(sum(0. if a == 999990 else a for a in amounts), 3)
                traces[var] = values[var] < .1 and 999990 in amounts
        for var, value in values.items():
            if value is not None:
                rows.append(dict(station=sid, time=iso(at), var=var, value=value, source='nmic',
                                 flags=[], trace=traces.get(var, False),
                                 **({'hours': int(var[4:])} if var.startswith('rain') else {})))
    return rows


# coordfix1 (2026-11-17, user「修一下」): stations placed from the GHCNh list keep old sites for many Chinese stations
# (e.g. 59287 Guangzhou 16 km, 59488 Zhuhai 35 km, 59658 Zhanjiang 22 km off). The NMIC station metadata the platform
# already holds (A0012_stations_api.json, CMA's own list, about 2,170 stations nationwide) replaces them when the site is
# more than COORD_FIX_KM away or the elevation differs by more than COORD_FIX_M. Hand-placed sites (other
# coordinate_source, e.g. Shenzhen 59493 at Zhuzilin) and stations missing from the list are left alone. The old values
# stay in station['coordinate_was']. Moving a site changes the scoring signature, so the next round rescores the window.
COORD_FIX_KM = 2.0
COORD_FIX_M = 50.0
GHCNH_SOURCE = 'GHCNh station list'


def correct_coordinates(table, metadata):
    """metadata: {station id: A0012 item}. Returns the list of corrections made (also kept in table['coordinate_fixes'])."""
    import math
    fixes = []
    for station in table.get('stations', []) + table.get('inactive', []):
        if station.get('coordinate_source') != GHCNH_SOURCE:
            continue
        meta = metadata.get(str(station.get('wmo') or station.get('id')))
        if meta is None:
            continue
        lon, lat, height = finite(meta.get('lon')), finite(meta.get('lat')), number(meta.get('height'))
        old_lon, old_lat, old_height = finite(station.get('lon')), finite(station.get('lat')), finite(station.get('elevation'))
        if lon is None or lat is None or old_lon is None or old_lat is None:
            continue
        km = math.hypot((lon - old_lon) * 111.2 * math.cos(math.radians(lat)), (lat - old_lat) * 111.2)
        metres = abs(height - old_height) if height is not None and old_height is not None else 0.0
        if km <= COORD_FIX_KM and metres <= COORD_FIX_M:
            continue
        station['coordinate_was'] = dict(lon=old_lon, lat=old_lat, elevation=old_height, source=GHCNH_SOURCE)
        station.update(lon=lon, lat=lat, coordinate_source='NMIC A0012_stations_api.json (coordfix1)', model_elevations={})
        if height is not None:
            station['elevation'] = height
        fixes.append(dict(id=station['id'], name=station.get('name') or meta.get('name'), km=round(km, 1),
                          metres=round(metres, 1), was=[old_lat, old_lon, old_height], now=[lat, lon, station.get('elevation')]))
    table['coordinate_fixes'] = dict(rule=f'GHCNh sites more than {COORD_FIX_KM:g} km or {COORD_FIX_M:g} m from NMIC A0012',
                                     count=len(fixes), stations=[f['id'] for f in fixes])
    return fixes


def _metadata(root=None):
    path = Path(os.environ.get('STATIONS_NMIC_METADATA', data_dir(root) / 'A0012_stations_api.json'))
    value = read_json(path)
    return None if value is None else {str(s['stationId']): s for s in value['data']}


def add_stations(table, root=None, end=None):
    """Merge only observed IDs; preserve all metadata/coordinates of old sites.

    Recent raw reports count for activity before the daily archive has been
    written. Old raw files alone cannot keep a station active indefinitely.
    """
    from station_common import now
    records, diagnostics = read_records(data_dir(root))
    if not records:
        metadata = _metadata(root)   # coordfix1: official sites even without fresh reports
        if metadata:
            correct_coordinates(table, metadata)
        return table
    folder = data_dir(root)
    path = Path(os.environ.get('STATIONS_NMIC_METADATA', folder / 'A0012_stations_api.json'))
    metadata = read_json(path)
    if metadata is None:
        raise ValueError(f'NMIC station metadata required: {path}')
    metadata = {str(s['stationId']): s for s in metadata['data']}
    provinces = read_json(ROOT / 'nmic_provinces.json', {})
    latest = {}
    for sid, at in records:
        latest[sid] = max(latest.get(sid, at), at)
    active = table.setdefault('stations', []); inactive = table.setdefault('inactive', [])
    existing = {s['id']: s for s in active+inactive}
    cutoff = (end or now()) - timedelta(days=45)
    diagnostics['missing_metadata'] = []
    diagnostics['outside_cmagd'] = []
    for sid in sorted(latest):
        if sid not in metadata:
            diagnostics['missing_metadata'].append(sid)
            continue
        meta = metadata[sid]
        lon, lat = finite(meta.get('lon')), finite(meta.get('lat'))
        if lon is None or lat is None or not in_cmagd(lon, lat):
            diagnostics['outside_cmagd'].append(sid)
            continue
        if sid in existing:
            station = existing[sid]
        else:
            station = dict(id=sid, wmo=sid, type='synop', name=meta['name'], lon=lon, lat=lat,
                           elevation=number(meta['height']), sources=[], model_elevations={},
                           coordinate_source='NMIC A0012_stations_api.json')
            (active if latest[sid] >= cutoff else inactive).append(station)
            existing[sid] = station
        if 'nmic' not in station['sources']:
            station['sources'].append('nmic')
        if sid in provinces:
            station['province_code'] = provinces[sid]
        if latest[sid] >= cutoff and station in inactive:
            inactive.remove(station); active.append(station)
    # The checked-in province map is the complete 260-site core (43/44/45/46).
    # No inference from station-number prefixes or previous run order is used.
    for station in active + inactive:
        station['cmagd_extended'] = (set(station.get('sources', ())) == {'nmic'} and
                                    str(station.get('province_code')) not in ('43', '44', '45', '46'))
        # Outer mixed-source sites are already part of the legacy products.
        # Importing their NMIC reports must not fill old gaps or invalidate
        # the EC01 point-request roster/certificates.
        station['nmic_cmagd_metadata_only'] = ('nmic' in station.get('sources', ()) and
            not station['cmagd_extended'] and str(station.get('province_code')) not in ('43', '44', '45', '46'))
    table['nmic'] = dict(**diagnostics, stations=len(latest), latest=iso(max(latest.values())))
    correct_coordinates(table, metadata)   # coordfix1
    return table
