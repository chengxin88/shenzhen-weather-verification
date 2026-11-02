"""Sample certified local archives at station points, with persistent discovery.

This module is for the offline worker only. It reuses native_archive validation
and Open-Meteo's manifest/field/sample readers; it never downloads anything.
"""
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'gods-eye-weather/weather_backend'))
from wn3_storage import logical_path, file_stat, read_text, open_read, load_array, read_path, union_glob

import json
import gzip
import os
import re
import sys
import time
import zipfile
import zlib
import numpy as np
from station_common import BOX, ROOT, UTC, VERSION, atomic_json, digest, iso, read_json, stamp, sync_directory

WEATHER = Path(os.environ.get('STATIONS_WEATHER_ROOT', '/home/chengyang/gods-eye-weather/weather-data'))
PRODUCTION = Path(os.environ.get('STATIONS_NATIVE_ROOT', '/home/chengyang/verification-test'))
OPENMETEO = Path('/home/chengyang/openmeteo-ecmwf')
NATIVE = {'ecmwf': 'ECMWF-IFS', 'gfs': 'NCEP-GFS', 'aifs': 'ECMWF-AIFS',
          'aigfs': 'NCEP-AIGFS', 'gefs': 'GEFS集合平均'}
FIELDS = ('surface_t', 'surface_rh', 'surface_u', 'surface_v', 'surface_msl',
          'rain3h', 'rain6h', 'orography', 'surface_elevation')
WORKERS=max(1,min(32,int(os.environ.get('STATIONS_SAMPLE_WORKERS','32'))))


@lru_cache(maxsize=1)
def native():
    import importlib.util
    spec=importlib.util.spec_from_file_location('_station_native_readers',ROOT/'native_archive.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    # A private reader instance: keep the shared grid module's Shenzhen BOX
    # untouched even when tests import both modules in the same interpreter.
    module.BOX = (BOX[0], BOX[2], BOX[1], BOX[3])
    return module


@lru_cache(maxsize=1)
def regional():
    if str(OPENMETEO) not in sys.path:
        sys.path.insert(0, str(OPENMETEO))
    import openmeteo_regional
    return openmeteo_regional


def file_signature(path):
    path=read_path(path)
    try:
        stat = file_stat(path)
        return [stat.st_mtime_ns, stat.st_size]
    except FileNotFoundError:
        return None


def interpolate(array, x, y, targets, nearest=False):
    """Index only 1/4 source cells per target; no materialized global fields."""
    x = np.asarray(x); y = np.asarray(y); targets = np.asarray(targets)
    fx = (targets[:, 0] - x[0]) / (x[1] - x[0])
    fy = (targets[:, 1] - y[0]) / (y[1] - y[0])
    inside = (fx >= -1e-8) & (fx <= len(x) - 1 + 1e-8) & (fy >= -1e-8) & (fy <= len(y) - 1 + 1e-8)
    fx = np.clip(fx, 0, len(x) - 1); fy = np.clip(fy, 0, len(y) - 1)
    if nearest:
        result = np.asarray(array[np.rint(fy).astype(int), np.rint(fx).astype(int)], dtype=float)
    else:
        i = np.minimum(np.floor(fx).astype(int), len(x) - 2)
        j = np.minimum(np.floor(fy).astype(int), len(y) - 2)
        dx, dy = fx - i, fy - j
        result = (array[j, i] * (1 - dx) * (1 - dy) + array[j, i + 1] * dx * (1 - dy)
                  + array[j + 1, i] * (1 - dx) * dy + array[j + 1, i + 1] * dx * dy)
    return np.where(inside, result, np.nan)


def dewpoint(t, rh):
    t, rh = np.asarray(t), np.asarray(rh)
    with np.errstate(divide='ignore', invalid='ignore'):
        g = np.log(rh / 100) + 17.625 * t / (243.04 + t)
        return np.where((rh > 0) & (rh <= 100.1), 243.04 * g / (17.625 - g), np.nan)


def interpolate_dewpoint(t, rh, x, y, targets):
    # Derive Td at the four native neighbours before bilinear interpolation.
    class Derived:
        def __getitem__(self, index):
            return dewpoint(t[index], rh[index])
    return interpolate(Derived(), x, y, targets)


def accumulate(intervals, end, hours):
    """Tile (end-hours,end] backwards. Reject gaps, overlaps and split steps.

    intervals maps ending lead -> (starting lead, station-value vector).
    Supports EC's native 1/3/6h steps and certified native rain3h/rain6h alike.
    """
    target = end - hours
    if target < 0:
        return None
    cursor = end; result = None
    while cursor > target:
        item = intervals.get(cursor)
        if item is None:
            return None
        start, values = item
        if not target <= start < cursor:
            return None
        result = np.array(values, dtype=float) if result is None else result + values
        cursor = start
    return result if cursor == target else None


def discover(root, end):
    """One initial scandir per model directory; subsequent runs use saved names
    and the published catalog. No repeated globs of the slow USB directories.
    """
    path = root / 'discovery.json'; saved = read_json(path, {'directories': {}})
    groups = defaultdict(dict); cutoff = end - timedelta(days=38)
    roots = [WEATHER / 'region-v4', PRODUCTION / 'cache/native-backfill', ROOT / 'cache/native-backfill']
    seen_roots = set()
    bootstrap = defaultdict(set)
    if not saved['directories']:
        # The platform already discovered the slow disk. Extract only its file
        # keys, without materializing its 87MB nested score objects or walking
        # ~15,000 entries per model directory again.
        index = PRODUCTION / 'native-scores.json'
        if index.is_file():
            with index.open('rb') as handle:
                content = handle.read()
            pattern = rb'"(/[^"\\]+/(?:ecmwf|gfs|aifs|aigfs|gefs)_\d{10}_f\d{3}(?:_global1)?\.json)"\s*:'
            for match in re.finditer(pattern, content):
                file = Path(match[1].decode('utf-8'))
                bootstrap[str(file.parent)].add(file.name)
            del content
    for parent in roots:
        for slug in NATIVE:
            directory = parent / slug
            if str(directory) in seen_roots or not directory.is_dir():
                continue
            seen_roots.add(str(directory)); key = str(directory)
            names = set(saved['directories'].get(key, []))
            catalog_file = directory / 'catalog.json'
            if key not in saved['directories'] and key in bootstrap:
                names.update(bootstrap[key])
            elif key not in saved['directories'] or not catalog_file.is_file():
                # Without a catalog (e.g. local GEFS backfill) new runs are only
                # visible to a directory listing; these directories are local.
                with os.scandir(directory) as entries:
                    names.update(e.name for e in entries if re.fullmatch(slug + r'_\d{10}_f\d{3}(?:_global1)?\.json', e.name))
            catalog = read_json(catalog_file, {})
            names.update(f['file'] for f in catalog.get('frames', []) if 'file' in f)
            # Once a run is known, remember all possible committed-frame names.
            # This finds late frames without re-enumerating the directory.
            runs = {m[1] for n in names if (m := re.fullmatch(slug + r'_(\d{10})_f\d{3}(?:_global1)?\.json', n))}
            for run_id in runs:
                run = datetime.strptime(run_id, '%Y%m%d%H').replace(tzinfo=UTC)
                if run < end - timedelta(days=2):
                    continue
                names.update(f'{slug}_{run_id}_f{lead:03}_global1.json' for lead in range(0, 169, 3))
            retained = []
            for name in sorted(names):
                match = re.fullmatch(slug + r'_(\d{10})_f(\d{3})(_global1)?\.json', name)
                if not match:
                    continue
                run = datetime.strptime(match[1], '%Y%m%d%H').replace(tzinfo=UTC)
                lead = int(match[2])
                if run < cutoff or lead > 168 or run > end:
                    continue
                retained.append(name)
                file = directory / name
                # Main archive precedes backfill, legacy precedes global as in
                # native_archive. Invalid present inputs are reported later.
                group=groups[slug,iso(run)]
                if lead not in group or not group[lead].is_file():
                    group[lead]=file
            saved['directories'][key] = retained
            atomic_json(path, saved)
            print('DISCOVER',slug,key,len(retained),'indexed names',flush=True)
    atomic_json(path, saved)
    return groups


def read_native_frame(path, slug, run, lead, targets, metadata=None):
    na = native(); d = metadata if metadata is not None else json.loads(path.read_text())
    x, y = na.validate_frame(d, NATIVE[slug], run, lead)
    result = {}; count = len(targets)
    if path.stem.endswith('_global1'):
        # Apply the same certified storage/grid checks without loading the old
        # cropped T/rain arrays and then reopening those files for station data.
        storage = d.get('storage', '')
        if storage not in (na.GLOBAL_STORAGE[slug], slug+'-regional-v1') or d.get('native_grid_degrees') != .25:
            raise ValueError('本地全球归档元数据不匹配')
        expected = ((49,37) if d.get('bounds') == [108.,18.,120.,27.] else (361,241)) if storage.endswith('-regional-v1') else (1441,721)
        if ((len(x),len(y)) != expected or (storage.endswith('-regional-v1') and
                not np.allclose([x[0],y[0],x[-1],y[-1]],d.get('bounds',[]),atol=1e-7,rtol=0))):
            raise ValueError('本地区域归档网格不匹配')
        directory = na.field_directory(path,d)
        def field(name):
            file = directory / (name + '.npy')
            if not file.is_file():
                return None
            array = np.load(str(file), mmap_mode='r', allow_pickle=False)
            if array.dtype != np.float32 or array.shape != (len(y), len(x)):
                raise ValueError('Native station field shape/dtype mismatch')
            return array
        # Validation above still admits the existing archive formats. Sample
        # the source axes, rather than the old 108–120 E cropped display box.
        # Narrow historical sources naturally return NaN outside their extent.
        t=field('surface_t')
        if t is not None:
            result['t']=interpolate(t,x,y,targets)
        rh=field('surface_rh');msl=field('surface_msl')
        if msl is not None:
            result['msl']=interpolate(msl,x,y,targets)
        if t is not None and rh is not None:
            result['td'] = interpolate_dewpoint(t, rh, x, y, targets)
        u, v = field('surface_u'), field('surface_v')
        if u is not None and v is not None:
            result['wind'] = np.hypot(interpolate(u, x, y, targets), interpolate(v, x, y, targets))
        for name in ('orography', 'surface_elevation'):
            unit=d.get('field_units',{}).get(name,d.get(name+'_unit'))
            if unit not in ('m','meter','metre'):
                continue
            array = field(name)
            if array is not None:
                result['elevation'] = interpolate(array, x, y, targets, nearest=True)
                break
        width = 3 if d.get('interval_hours') is None else d['interval_hours']
        rain = field('rain' + str(width) + 'h') if width in (3,6) else None
        if rain is not None and d.get('rain_start') and stamp(d['rain_start']) == run + timedelta(hours=lead - width):
            result['rain'] = interpolate(rain, x, y, targets, nearest=True)
            result['rain_start'] = lead - width
    else:
        layer = d.get('layers', {}).get('surface', {})
        def field(name):
            value = layer.get(name)
            if value is None:
                return None
            array = np.asarray(value, dtype=float)
            if array.size != len(y) * len(x):
                raise ValueError('Legacy station field dimensions mismatch')
            return array.reshape(len(y), len(x))
        t, rh = field('t'), field('rh')
        for key, array in [('t', t), ('msl', field('msl'))]:
            if array is not None:
                result[key] = interpolate(array, x, y, targets)
        if t is not None and rh is not None:
            result['td'] = interpolate_dewpoint(t, rh, x, y, targets)
        u, v = field('u'), field('v')
        if u is not None and v is not None:
            result['wind'] = np.hypot(interpolate(u, x, y, targets), interpolate(v, x, y, targets))
        if d.get('rain3h') is not None and d.get('rain_start') and stamp(d['rain_start']) == run + timedelta(hours=lead - 3):
            result['rain'] = interpolate(np.asarray(d['rain3h'], float).reshape(len(y), len(x)), x, y, targets, nearest=True)
            result['rain_start'] = lead - 3
    return result


def native_signature(path,saved=None):
    stat=file_signature(path)
    if stat is None:
        raise FileNotFoundError(str(path))
    data=(saved['metadata'] if saved and saved.get('path')==str(path) and saved.get('meta_stat')==stat
          else json.loads(path.read_text()))
    fields = native().field_directory(path, data)
    field_stats = []
    if path.stem.endswith('_global1'):
        try:
            namespace = fields.stat()
            namespace = [namespace.st_dev,namespace.st_ino,namespace.st_mtime_ns,namespace.st_ctime_ns]
        except FileNotFoundError:
            namespace = None
        # A directory's namespace identity changes on addition/removal/rename.
        # Only negative lookups may be reused. Existing files are always stat'ed
        # so an in-place field revision with unchanged JSON is still detected.
        same = (saved is not None and saved.get('field_directory')==str(fields)
                and 'field_namespace' in saved and saved['field_namespace']==namespace
                # Filesystems can give several namespace edits the same coarse
                # timestamp. Never cache negative lookups made near such edits.
                and (namespace is None or saved.get('field_checked_at_ns',0)
                     - max(namespace[2:]) > 2_000_000_000))
        old_fields = dict(saved.get('field_stats',[])) if same else {}
        field_stats = [[name,None if name in old_fields and old_fields[name] is None
                        else file_signature(fields/(name+'.npy'))] for name in FIELDS]
        if saved is not None:
            saved.update(field_directory=str(fields),field_namespace=namespace,field_stats=field_stats,
                         field_checked_at_ns=saved.get('field_checked_at_ns',0) if same else time.time_ns())
    signature = [str(path), stat,
                 field_stats]
    return signature,data,stat


def save_samples(path, leads, samples, state=None):
    arrays = {'leads': np.asarray(leads, dtype=np.int16)}
    variables = set().union(*(s for s in samples.values())) if samples else set()
    count = next((len(v) for s in samples.values() for k, v in s.items() if k != 'rain_start'), 0)
    for variable in variables:
        arrays[variable] = np.asarray([samples[h].get(variable, np.nan if variable == 'rain_start' else np.full(count, np.nan)) for h in leads], dtype=np.float64)
    temporary = path.with_suffix('.next')
    with temporary.open('w+b') as handle:
        np.savez_compressed(handle, **arrays)
        if state is not None:
            # A ZIP comment is ignored by old np.load readers. Adding a scalar
            # NPZ member would break their assumption that every member is an
            # array indexed by lead, making a code rollback need cache repair.
            comment = b'STATION_SAMPLE_V2\0'+zlib.compress(json.dumps(state,allow_nan=False,separators=(',',':')).encode())
            if len(comment) > 65535:raise ValueError('Station sample certificate exceeds ZIP comment limit')
            with zipfile.ZipFile(handle,'a') as archive:archive.comment=comment
        handle.flush(); os.fsync(handle.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def load_samples(path, wanted=None):
    with np.load(path, allow_pickle=False) as archive:
        leads = archive['leads'].tolist()
        arrays={key:archive[key] for key in archive.files if key not in ('leads','_state')}
        return {int(lead): {key:array[i] for key,array in arrays.items()} for i, lead in enumerate(leads) if wanted is None or int(lead) in wanted}


def sample_state(path, fallback):
    """New arrays and their certificate share one atomic NPZ replacement.

    Sidecars remain readable for legacy caches and diagnostics. A killed worker
    cannot leave a new sample file paired with an old certificate, including
    when its original raw archives have already been pruned.
    """
    if path.is_file():
        with load_array(path,allow_pickle=False) as archive:
            prefix = b'STATION_SAMPLE_V2\0'
            if archive.zip.comment.startswith(prefix):
                return json.loads(zlib.decompress(archive.zip.comment[len(prefix):])), True
            if '_state' in archive.files:
                return json.loads(str(archive['_state'])), True
    try:
        return read_json(fallback,{}), False
    except (OSError,ValueError):
        return {}, False


def regional_series(reg, ident, data, name, wanted, target, nearest=False):
    """Sample all wanted leads of one regional field, reading only the station band.

    The regional cache sits on /mnt/d (drvfs, ~4 MB/s), where mmap page faults
    plus the reader's full-file checksum took 15-55 min per run. The checksum is
    verified at ingest (openmeteo_regional.download_field) and the files are
    immutable afterwards; mtime/size are part of the run signature. Here only the
    rows/columns around the stations are read, and sampling uses the same
    RegularGridInterpolator settings as reg.sample on the sub-grid.
    """
    from scipy.interpolate import RegularGridInterpolator
    path = reg.directory(ident) / (name + '.npy'); out = {}
    lat, lon = np.asarray(reg.LAT), np.asarray(reg.LON)
    shape = (len(reg.expected_leads(ident)), len(lat), len(lon))
    points = np.asarray(target, dtype=float)
    r0 = max(0, int(np.searchsorted(lat, points[:, 1].min())) - 2); r1 = min(len(lat), int(np.searchsorted(lat, points[:, 1].max())) + 2)
    c0 = max(0, int(np.searchsorted(lon, points[:, 0].min())) - 2); c1 = min(len(lon), int(np.searchsorted(lon, points[:, 0].max())) + 2)
    with path.open('rb') as handle:
        version = np.lib.format.read_magic(handle)
        reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
        found, fortran, dtype = reader(handle)
        offset = handle.tell()
        if np.dtype(dtype) != np.float32 or fortran or tuple(found) != shape or len(data['leads']) != shape[0]:
            raise ValueError('Regional array dimensions mismatch')
        if path.stat().st_size != offset + shape[0] * shape[1] * shape[2] * 4:
            raise ValueError('Regional field truncated')
        row = shape[2] * 4; step = shape[1] * row
        for index, lead in enumerate(data['leads']):
            if lead not in wanted:
                continue
            handle.seek(offset + index * step + r0 * row); raw = handle.read((r1 - r0) * row)
            band = np.frombuffer(raw, dtype='<f4').reshape(r1 - r0, shape[2])[:, c0:c1]
            out[lead] = RegularGridInterpolator((lat[r0:r1], lon[c0:c1]), band, method='nearest' if nearest else 'linear',
                                                bounds_error=False, fill_value=np.nan)(points[:, ::-1])
    return out


class SamplePlan(Mapping):
    """A source-checked run whose small station arrays are loaded on demand."""
    def __init__(self, leads, legacy_signature, loader, cache_path=None):
        self.leads = tuple(sorted(leads))
        self.legacy_signature = legacy_signature
        self.loader = loader
        self.cache_path = cache_path

    def __iter__(self):
        return iter(self.leads)

    def __len__(self):
        return len(self.leads)

    def __getitem__(self, lead):
        if lead not in self.leads:
            raise KeyError(lead)
        return self.load({lead})[lead]

    def load(self, wanted):
        # All three native tilings need at most 24 preceding hours. No earlier
        # instantaneous values are paired; pair_run uses only requested valids.
        needed = {h for h in self.leads if any(max(0, lead-24) <= h <= lead for lead in wanted)}
        return self.loader(needed) if needed else {}


def forecast_runs(root, stations, end, lazy=False, timings=None):
    """Discover source identities first; sample only a run that needs pairing.

    Source identities cover all committed <=168h leads, independent of the
    scoring window. Advancing end therefore changes observation digests, not
    the forecast identity. Old window-dependent identities are used solely for
    exact migration of jobs and pre-existing station sample caches.
    """
    target = [(s['lon'], s['lat']) for s in stations]
    station_sig = digest([[s['id'], s['lon'], s['lat']] for s in stations])
    out = root / 'forecast'; out.mkdir(exist_ok=True)
    memo_path = out / 'index.json'; memo = read_json(memo_path, {})
    # Retain the old point order when expanding a station roster. Historical
    # source frames may already be pruned; their certified values remain valid
    # for unchanged station coordinates, but supply no data for new stations.
    points = [[s['id'], s['lon'], s['lat']] for s in stations]
    layouts_path = out / 'station-layouts.json'
    layouts = read_json(layouts_path, {})
    generation = read_json(root/'current.json', {}).get('generation')
    previous = read_json(root/'snapshots'/generation/'meta.json', {}) if generation else {}
    previous_points = [[s['id'], s['lon'], s['lat']] for s in previous.get('stations', [])]
    if previous_points:
        layouts[digest(previous_points)] = previous_points
    layouts[station_sig] = points
    atomic_json(layouts_path, layouts)
    destinations = {tuple(point): i for i, point in enumerate(points)}

    def remapping(state):
        source = layouts.get(state.get('station_signature'))
        if state.get('station_signature') == station_sig or not source:
            return None
        if not all(tuple(point) in destinations for point in source):
            return None  # moving a station must invalidate its old certificate
        return [destinations[tuple(point)] for point in source]

    def cached_samples(path, wanted=None):
        values = load_samples(path, wanted)
        state, _ = sample_state(path, path.with_suffix('.sample-state.json'))
        indices = remapping(state)
        if indices is not None:
            for sample in values.values():
                for name, array in list(sample.items()):
                    if name == 'rain_start':
                        continue
                    if np.shape(array) != (len(indices),):
                        raise ValueError('Station cache does not match its certified point order')
                    expanded = np.full(len(points), np.nan)
                    expanded[indices] = array
                    sample[name] = expanded
        return values

    def outside_added_points(state, bounds):
        if remapping(state) is None:
            return False
        original = {tuple(point) for point in layouts[state['station_signature']]}
        added = [point for point in points if tuple(point) not in original]
        west, south, east, north = bounds
        return bool(added) and all(lon < west or lon > east or lat < south or lat > north
                                   for _, lon, lat in added)

    def plan(key, leads, source_sig, legacy_sig, per_lead, sample_one, issues,
             retained=(), certified=None, certify=False, layout_reuse=()):
        file = out / (key+'.npz')
        state_path = out / (key+'.sample-state.json')
        state, embedded = sample_state(file,state_path)
        stat = file_signature(file)
        trusted = (state.get('station_signature') == station_sig and stat is not None
                   and (embedded or state.get('sample_file_signature') == stat))
        migrating = (stat is not None and remapping(state) is not None
                     and (embedded or state.get('sample_file_signature') == stat))
        if trusted:
            old_sources = {int(h): sig for h,sig in state['sources']}
        elif migrating:
            # Available raw frames are sampled for the expanded roster. Only
            # pruned frames reuse remapped old points, with NaN for new sites.
            old_sources = {int(h): sig for h,sig in state['sources']
                           if int(h) in retained or int(h) in layout_reuse}
        else:
            old_sources = {}
            if not state and stat is not None and memo.get(key) == legacy_sig:
                old_sources = dict(per_lead)
            elif certified:
                old_sources = {h:per_lead[h] for h in retained}
        # Merely reading lead names does not decompress the forecast arrays.
        if stat is not None:
            with np.load(file, allow_pickle=False) as archive:
                cached_leads = set(archive['leads'].tolist())
        else:
            cached_leads = set()
        if old_sources and not trusted and not state:
            atomic_json(state_path,dict(version=VERSION,station_signature=station_sig,
                        sample_file_signature=stat,sources=sorted(old_sources.items())),durable=True)
        def load(needed):
            reusable = {h for h in needed if h in cached_leads and old_sources.get(h) == per_lead.get(h)}
            values = cached_samples(file, reusable) if reusable else {}
            missing = sorted(needed-reusable)
            print('SAMPLES',key,len(needed),'loaded_leads',len(missing),'sampled_leads',flush=True)
            if missing:
                with ThreadPoolExecutor(max_workers=min(WORKERS,8)) as pool:
                    for lead,sample,errors in pool.map(sample_one, missing):
                        values[lead] = sample; issues.extend(errors)
                # Preserve old samples for leads not needed on this tick. Their
                # individual identities decide reuse on a later tick.
                compatible = state.get('station_signature') == station_sig or migrating or bool(old_sources)
                merged = cached_samples(file) if stat is not None and compatible else {}
                merged.update(values)
                updated = dict(old_sources)
                updated.update((h,per_lead[h]) for h in missing)
                updated_state = dict(sample_state(file,state_path)[0],version=VERSION,
                                     station_signature=station_sig,sources=sorted(updated.items()))
                save_samples(file,sorted(merged),merged,state=updated_state)
                atomic_json(state_path,dict(updated_state,sample_file_signature=file_signature(file)),durable=True)
            elif migrating:
                updated_state = dict(state, version=VERSION, station_signature=station_sig,
                                     sources=sorted(old_sources.items()))
                merged = cached_samples(file)
                save_samples(file, sorted(merged), merged, state=updated_state)
                atomic_json(state_path,dict(updated_state,sample_file_signature=file_signature(file)),durable=True)
            elif not trusted and stat is not None:
                atomic_json(state_path, dict(version=VERSION,station_signature=station_sig,
                    sample_file_signature=stat,sources=sorted(old_sources.items())),durable=True)
            if certify:
                atomic_json(out/(key+'.certified.json'),
                            dict(sample_state(file,state_path)[0],sample_file_signature=file_signature(file)),durable=True)
            return values
        return SamplePlan(leads, legacy_sig, load, file)

    started = time.monotonic()
    groups = discover(root, end)
    discovered = time.monotonic()-started
    if timings is not None:timings['discover'] = discovered
    print('TIMING discover',round(discovered,3),'seconds',flush=True)
    for (slug, run_text), paths in sorted(groups.items()):
        run = stamp(run_text); key = slug+'_'+run.strftime('%Y%m%d%H')
        file = out / (key+'.npz'); issues = []; metadata = {}; signatures = []
        certified, embedded = sample_state(file,out/(key+'.certified.json'))
        trusted = (certified.get('station_signature') == station_sig and file.is_file()
                   and (embedded or certified.get('sample_file_signature') == file_signature(file)))
        migrating = (file.is_file() and remapping(certified) is not None
                     and (embedded or certified.get('sample_file_signature') == file_signature(file)))
        if embedded and trusted:
            certificate_path = out/(key+'.certified.json')
            try:previous_certificate = read_json(certificate_path,{})
            except (OSError,ValueError):previous_certificate = {}
            if previous_certificate.get('sample_file_signature') != file_signature(file):
                atomic_json(certificate_path,dict(certified,sample_file_signature=file_signature(file)),durable=True)
        cached_leads = set()
        if trusted or migrating:
            with np.load(file,allow_pickle=False) as archive:
                cached_leads = set(archive['leads'].tolist())
        cached_sources = {int(h):sig for h,sig in certified.get('sources',[])} if trusted or migrating else {}
        source_file = out/(key+'.sources.json.gz')
        if source_file.is_file():
            try:
                with gzip.open(source_file,'rt') as handle:source_cache=json.load(handle)
            except (OSError,ValueError,EOFError):source_cache={}
        else:source_cache={}
        cache_changed = not source_file.is_file()
        retained = set()
        def inspect(item):
            lead,path=item
            try:
                saved = dict(source_cache.get(str(lead),{}))
                signature,data,stat=native_signature(path,saved)
                saved.update(path=str(path),meta_stat=stat,metadata=data)
                return lead,signature,data,stat,None,saved
            except FileNotFoundError:
                if lead in cached_sources and lead in cached_leads:
                    return lead,cached_sources[lead],None,'certified-cache',None,None
                return lead,None,None,None,None,None
            except (OSError, ValueError) as exc:
                return lead,'invalid',None,None,str(exc),None
        native()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for lead,signature,data,stat,error,saved in pool.map(inspect,sorted(paths.items())):
                if signature is not None:signatures.append([lead,signature])
                if data is not None:
                    metadata[lead]=data
                    if saved != source_cache.get(str(lead)):
                        source_cache[str(lead)]=saved
                        cache_changed = True
                elif stat=='certified-cache':retained.add(lead)
                if error:issues.append([slug,run_text,lead,error])
        if cache_changed:
            temporary=source_file.with_suffix('.next')
            with gzip.open(temporary,'wt') as handle:json.dump(source_cache,handle,allow_nan=False,separators=(',',':'))
            temporary.replace(source_file)
        active = lambda h: end-timedelta(days=32) <= run+timedelta(hours=h) <= end
        legacy_sig = digest([VERSION,station_sig,[[h,s] for h,s in signatures if active(h)]])
        sig = digest([VERSION,station_sig,signatures])
        leads = {h for h in metadata.keys() | retained if active(h)}
        if not leads and not any(active(h) for h in paths):continue
        def sample_one(lead):
            try:return lead,read_native_frame(paths[lead],slug,run,lead,target,metadata[lead]),[]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                return lead,{},[[slug,run_text,lead,str(exc)]]
        # Plans are consumed before advancing this generator; closures therefore
        # refer to exactly this run, and hold only one run's source metadata.
        issues.extend(read_json(out/(key+'.issues.json'),[]))
        layout_reuse = {h for h,d in metadata.items()
                        if outside_added_points(certified,[d['lon'][0],d['lat'][0],d['lon'][-1],d['lat'][-1]])}
        samples = plan(key,leads,sig,legacy_sig,dict(signatures),sample_one,issues,retained,
                       certified if trusted else None,True,layout_reuse)
        samples.precip_metadata = source_cache
        yield slug,run,samples if lazy else samples.load(set(samples)),sig,issues

    reg = regional()
    for directory in sorted(reg.root().iterdir()):
        if not re.fullmatch(r'\d{10}',directory.name) or not (directory/'run.json').is_file():continue
        run = datetime.strptime(directory.name,'%Y%m%d%H').replace(tzinfo=UTC)
        if not end-timedelta(days=38) <= run <= end:continue
        key='ec01_'+directory.name; issues=[]
        try:
            manifest=reg.manifest(directory.name)
            precip_meta=manifest['fields']['precipitation']; source_times=set(precip_meta.get('source_times',[]))
            if precip_meta.get('unit')!='mm' or not {int((run+timedelta(hours=h)).timestamp()) for h in manifest['leads'] if h}<=source_times:
                raise ValueError('Regional precipitation source time axis/unit mismatch')
            names=['temperature_2m','dew_point_2m','pressure_msl','wind_u_component_10m','wind_v_component_10m','precipitation']
            signatures=[[name,file_signature(directory/(name+'.npy')),manifest['fields'][name]['sha256']] for name in names]
            active=[h for h in manifest['leads'] if end-timedelta(days=32)<=run+timedelta(hours=h)<=end]
            sig=digest([VERSION,station_sig,manifest,signatures])
            legacy_sig=digest([VERSION,station_sig,manifest,signatures,active])
            # Six shared series reads, restricted to needed leads, rather than
            # opening six large fields independently for every single lead.
            file=out/(key+'.npz'); state_path=out/(key+'.sample-state.json')
            if file.is_file() and not state_path.is_file() and memo.get(key)==legacy_sig:
                atomic_json(state_path,dict(version=VERSION,station_signature=station_sig,source=sig,sample_file_signature=file_signature(file)))
            def load(needed):
                state,embedded=sample_state(file,state_path)
                trusted=(state.get('version')==VERSION and state.get('station_signature')==station_sig and state.get('source')==sig
                         and (embedded or state.get('sample_file_signature')==file_signature(file)))
                old=load_samples(file) if file.is_file() and (trusted or (not state and memo.get(key)==legacy_sig)) else {}
                missing=needed-old.keys()
                print('SAMPLES',key,len(needed),'loaded_leads',len(missing),'sampled_leads',flush=True)
                if missing:
                    with ThreadPoolExecutor(max_workers=len(names)) as pool:
                        series=dict(zip(names,pool.map(lambda name:regional_series(reg,directory.name,manifest,name,missing,target,nearest=name=='precipitation'),names)))
                    for lead in sorted(missing):
                        values=dict(t=series['temperature_2m'][lead],td=series['dew_point_2m'][lead],msl=series['pressure_msl'][lead],
                                    wind=np.hypot(series['wind_u_component_10m'][lead],series['wind_v_component_10m'][lead]))
                        if lead:values.update(rain=series['precipitation'][lead],rain_start=lead-reg.ec.native_step(lead))
                        old[lead]=values
                    save_samples(file,sorted(old),old,state=dict(state,version=VERSION,station_signature=station_sig,source=sig))
                if file.is_file():atomic_json(state_path,dict(version=VERSION,station_signature=station_sig,source=sig,sample_file_signature=file_signature(file)))
                reg.field_file.cache_clear()
                return {h:old[h] for h in needed}
            samples = SamplePlan(active,legacy_sig,load,file)
            yield 'ec01',run,samples if lazy else samples.load(set(samples)),sig,issues
        except (OSError,ValueError,KeyError) as exc:
            yield 'ec01',run,{},digest([VERSION,str(exc)]),[['ec01',iso(run),None,str(exc)]]
        finally:reg.field_file.cache_clear()

    na=native();wnroot=WEATHER/'region-v4/weathernext3'
    for path in sorted(union_glob(wnroot,'run_??????????.json')):
        issues=[]
        try:
            run,directory,committed=na.wn3_manifest(path)
            if not end-timedelta(days=38)<=run<=end:continue
            all_leads=sorted(h for h in committed if h<=168)
            active=[h for h in all_leads if end-timedelta(days=32)<=run+timedelta(hours=h)<=end]
            key='wn3_'+run.strftime('%Y%m%d%H')
            entries=[(h,name,wnroot/directory/run.strftime('%Y%m%d%H')/f'f{h:03}_{name}.npz') for h in all_leads for name in ('surface_t','rain1h')]
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                signatures=[[h,name,stat] for (h,name,_),stat in zip(entries,pool.map(file_signature,(s[2] for s in entries)))]
            manifest=read_json(read_path(path))
            sig=digest([VERSION,station_sig,manifest,signatures,list(BOX)])
            legacy_sig=digest([VERSION,station_sig,manifest,[s for s in signatures if s[0] in active]])
            manifest_sig=digest(manifest)
            per_lead={h:[manifest_sig,[s for s in signatures if s[0]==h],list(BOX)] for h in all_leads}
            def sample_one(lead):
                values={};errors=[]
                for variable,name in [('t','surface_t'),('rain','rain1h')]:
                    source=read_path(wnroot/directory/run.strftime('%Y%m%d%H')/f'f{lead:03}_{name}.npz')
                    if not source.is_file():continue
                    try:
                        x,y,array=na.load_wn3_field(source,run,lead,name,directory)
                        values[variable]=interpolate(array,x,y,target,nearest=variable=='rain')
                        if variable=='rain':values['rain_start']=lead-1
                    except na.WN3_ERRORS as exc:errors.append(['wn3',iso(run),lead,str(exc)])
                return lead,values,errors
            old_state,_ = sample_state(out/(key+'.npz'),out/(key+'.sample-state.json'))
            layout_reuse = active if outside_added_points(old_state,na.WN3_SOURCES[directory][1]) else ()
            samples = plan(key,active,sig,legacy_sig,per_lead,sample_one,issues,layout_reuse=layout_reuse)
            yield 'wn3',run,samples if lazy else samples.load(set(samples)),sig,issues
        except na.WN3_ERRORS as exc:
            yield 'wn3',None,{},'', [['wn3',path.name,None,str(exc)]]
