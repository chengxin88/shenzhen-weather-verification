"""Decode, retain QC flags, compare independent feeds, and choose observations."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import csv
import gzip
import json
import math
from pathlib import Path
from station_common import UTC, VARIABLES, atomic_json, finite, iso, stamp
from synop_decode import decode

GH_FIELDS = {'t': 'temperature', 'td': 'dew_point_temperature', 'msl': 'sea_level_pressure',
             'wind': 'wind_speed', 'rain6': 'precipitation_6_hour', 'rain24': 'precipitation_24_hour'}


def row(station, at, variable, value, source, **extra):
    return dict(station=station, time=at, var=variable, value=value, source=source,
                flags=[], trace=False, **extra)


def ogimet_rows(raw, station_ids, cutoff, end):
    reports = {}; errors = []; duplicates = 0
    for path in sorted((raw / 'ogimet').glob('*.txt')):
        with path.open() as handle:
            for line in handle:
                if line[:5] not in station_ids:
                    continue
                try:
                    report = decode(line)
                except ValueError as exc:
                    errors.append(dict(file=path.name, line=line.strip(), error=str(exc)))
                    continue
                if report is None or not iso(cutoff) <= report['time'] <= iso(end):
                    continue
                key = report['station'], report['time']
                previous = reports.get(key)
                if previous:
                    duplicates += 1
                    if report['correction'] < previous['correction']:
                        continue
                    if report['correction'] == previous['correction'] and report['raw'] != previous['raw']:
                        report['flags'].append('duplicate_conflict')
                reports[key] = report
    rows = []; unknown = []
    for report in reports.values():
        if report['nil']:
            continue
        for variable in ('t', 'td', 'msl', 'wind'):
            value = report['values'].get(variable)
            if value is not None:
                item = row(report['station'], report['time'], variable, value, 'ogimet',
                           correction=report['correction'])
                item['flags'] += report['flags']; rows.append(item)
        amounts = defaultdict(list)
        for precip in report['precip']:
            if precip['hours'] not in (6, 24):
                unknown.append(dict(station=report['station'], time=report['time'],
                                    iR=report['iR'], **precip))
            elif precip['value'] is not None:
                amounts[precip['hours']].append(precip)
        for hours, parts in amounts.items():
            p = parts[-1]
            item = row(report['station'], report['time'], 'rain' + str(hours), p['value'], 'ogimet',
                       hours=hours, raw_group=p['group'], lower_bound=p['lower_bound'])
            item['trace'] = p['trace']; item['flags'] += report['flags']
            if any(abs(q['value'] - p['value']) > 1e-6 for q in parts):
                item['flags'].append('conflicting_precip_groups')
            # A censored lower bound cannot be used as an exact amount.
            if p['lower_bound']:
                item['flags'].append('precip_lower_bound')
            rows.append(item)
    return rows, list(reports.values()), unknown, dict(decode_errors=errors, duplicates=duplicates)


def ghcnh_rows(raw, stations, cutoff, end):
    rows = []; duplicates = 0; rejected_codes = Counter()
    for station in stations:
        ident = station.get('ghcnh_id')
        if not ident:
            continue
        path = raw / 'ghcnh' / (ident + '_2026.psv')
        if not path.is_file():
            continue
        with path.open(newline='') as handle:
            for record in csv.DictReader(handle, delimiter='|'):
                if record.get('STATION', record.get('Station_ID')) != ident:
                    raise ValueError('GHCNh station identity mismatch: ' + path.name)
                date = record.get('DATE')
                if not date:
                    date = datetime(*(int(record[k]) for k in ('Year', 'Month', 'Day', 'Hour', 'Minute')), tzinfo=UTC).isoformat()
                at = stamp(date + 'Z' if not date.endswith('Z') and '+' not in date else date)
                if not cutoff <= at <= end:
                    continue
                for variable, field in GH_FIELDS.items():
                    value = finite(record.get(field))
                    if value is None:
                        continue
                    qc = record.get(field + '_Quality_Code', '')
                    measurement = record.get(field + '_Measurement_Code', '')
                    item = row(station['id'], iso(at), variable, value, 'ghcnh',
                               quality_code=qc, measurement_code=measurement,
                               report_type=record.get(field + '_Report_Type', ''))
                    source_code = record.get(field + '_Source_Code', '')
                    # GHCNh v1.1 Table 3 is source-dependent. In particular,
                    # code 5 for source 220 means REMOVED, not "passed".
                    accepted = {'1'} if source_code in {'220', '221', '222', '223', '347', '348'} else (
                        {'0', '1', '4', '5', '9'} if source_code in {'313', '314', '315', '322', '335', '343', '344', '346'} else {''})
                    if qc not in accepted:
                        item['flags'].append('ghcnh_quality_' + (qc or 'blank'))
                        rejected_codes[qc or 'blank'] += 1
                    item['trace'] = measurement == 'T'
                    if item['trace']:
                        item['value'] = 0.
                    rows.append(item)
    return rows, dict(rejected_quality_codes=dict(rejected_codes))


def iem_rows(raw, station_ids, cutoff, end):
    result = {}
    for path in sorted((raw / 'iem').glob('*.csv')):
        with path.open(newline='') as handle:
            for record in csv.DictReader(handle):
                station = record.get('station')
                if station not in station_ids:
                    continue
                at = stamp(record['valid'].replace(' ', 'T') + 'Z')
                if not cutoff <= at <= end:
                    continue
                for variable, key in [('t', 'tmpf'), ('td', 'dwpf'), ('wind', 'sknt')]:
                    value = finite(record.get(key))
                    if value is None:
                        continue
                    value = (value - 32) * 5 / 9 if key in ('tmpf', 'dwpf') else value * .5144444444444445
                    item = row(station, iso(at), variable, value, 'iem')
                    identity = station, iso(at), variable
                    prev = result.get(identity)
                    if prev and abs(prev['value'] - value) > .00001:
                        item['flags'].append('duplicate_conflict')
                    result[identity] = item
    return list(result.values())


def seasonal_limits(at):
    month = (at + timedelta(hours=8)).month
    if month in (12, 1, 2):
        return -20., 40., 250., 500.
    if month in (3, 4, 5):
        return -10., 45., 450., 800.
    if month in (6, 7, 8):
        return 0., 50., 650., 1200.
    return -10., 45., 550., 1000.


def qc_rows(rows):
    """Flag values; never rewrite the measurements or interpolate gaps."""
    grouped = defaultdict(list); temperatures = {}
    for item in rows:
        at = stamp(item['time']); variable = item['var']; value = item['value']
        lo, hi, rain6, rain24 = seasonal_limits(at)
        if variable.startswith('rain'):
            hours = int(variable[4:])
            profiles = [seasonal_limits(at - timedelta(hours=h)) for h in range(hours + 1)]
            bounds = (0, max(p[2 if hours == 6 else 3] for p in profiles))
        else:
            bounds = {'t': (lo, hi), 'td': (-100, hi + .5), 'msl': (650, 1150), 'wind': (0, 60)}[variable]
        if not bounds[0] <= value <= bounds[1]:
            item['flags'].append('physical_range')
        if variable == 't':
            temperatures[item['source'], item['station'], item['time']] = value
        grouped[item['source'], item['station'], variable].append(item)
    for item in rows:
        t = temperatures.get((item['source'], item['station'], item['time']))
        if item['var'] == 'td' and t is not None and item['value'] > t + .5:
            item['flags'].append('dewpoint_above_temperature')
    for (_, _, variable), items in grouped.items():
        if variable.startswith('rain'):
            continue
        items.sort(key=lambda r: r['time'])
        for previous, current in zip(items, items[1:]):
            hours = (stamp(current['time']) - stamp(previous['time'])).total_seconds() / 3600
            if 0 < hours <= 6 and abs(current['value'] - previous['value']) > max(1, hours) * {'t': 20, 'td': 25, 'wind': 40, 'msl': 30}[variable]:
                current['flags'].append('temporal_jump')
    return rows


def deduplicate(rows):
    unique={}
    for item in rows:
        key=item['source'],item['station'],item['time'],item['var']
        previous=unique.get(key)
        if previous is None:
            unique[key]=item
        else:
            previous['duplicate_reports']=previous.get('duplicate_reports',0)+1
            if abs(previous['value']-item['value'])>1e-6 or previous['trace']!=item['trace']:
                if 'duplicate_conflict' not in previous['flags']:
                    previous['flags'].append('duplicate_conflict')
    return list(unique.values())


def agreement(rows, unknown, root):
    good = {(r['source'], r['station'], r['time'], r['var']): r for r in rows if not r['flags']}
    table = []; mismatch_path = root / 'agreement-mismatches.csv.gz'
    with gzip.open(mismatch_path, 'wt', newline='') as handle:
        writer = csv.writer(handle); writer.writerow(['station', 'time', 'var', 'ogimet', 'ghcnh', 'difference'])
        for variable in VARIABLES:
            count = exact = 0; total = 0.; examples = []
            for key, item in good.items():
                if key[0] != 'ogimet' or key[-1] != variable:
                    continue
                other = good.get(('ghcnh', *key[1:]))
                if other is None:
                    continue
                difference = item['value'] - other['value']; count += 1
                total += abs(difference); exact += abs(difference) < 1e-5
                if abs(difference) >= 1e-5:
                    mismatch = [item['station'], item['time'], variable, item['value'], other['value'], difference]
                    writer.writerow(mismatch)
                    if len(examples) < 20:
                        examples.append(mismatch)
            table.append(dict(var=variable, n=count, exact_percent=100 * exact / count if count else None,
                              mean_abs_diff=total / count if count else None, mismatches=examples))
    ir3 = [p for p in unknown if p.get('iR') == 3 and p['group'] == 'iR=3']
    evidence = {}
    for variable in ('rain6', 'rain24'):
        matches = [good['ghcnh', p['station'], p['time'], variable]['value'] for p in ir3
                   if ('ghcnh', p['station'], p['time'], variable) in good]
        evidence[variable] = dict(n=len(matches), zero=sum(v == 0 for v in matches), nonzero=sum(v > 0 for v in matches))
    value = dict(table=table, ir3=dict(reports=len(ir3), comparison=evidence,
                 conclusion='iR=3未携带累计时段；即使同期GHCNh为零也不能证明时段。v1始终不把省略组推定为6h/24h零降水。'))
    atomic_json(root / 'agreement.json', value)
    return value


def select_observations(rows, stations):
    """OGIMET first, QC-passed GHCNh fills only absent/bad source variables.

    METAR's closest whole report within ±10 minutes is chosen per station/hour,
    with earlier report as deterministic tie breaker; missing fields stay missing.
    """
    types = {s['id']: s['type'] for s in stations}; result = {}; closest={}
    for item in rows:
        if types.get(item['station'])!='metar' or item['var'] not in ('t','td','wind'):
            continue
        at=stamp(item['time']);nominal=(at+timedelta(minutes=30)).replace(minute=0,second=0,microsecond=0)
        offset=abs((at-nominal).total_seconds());key=item['station'],iso(nominal)
        if offset<=600 and (key not in closest or (offset,item['time'])<closest[key]):
            closest[key]=(offset,item['time'])
    for item in sorted(rows, key=lambda r: (r['time'], r['source'])):
        if item['flags']:
            continue
        at = stamp(item['time'])
        if types.get(item['station']) == 'metar':
            if item['var'] not in ('t', 'td', 'wind'):
                continue
            nominal = (at + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
            offset = abs((at - nominal).total_seconds())
            if offset > 600:
                continue
            if closest.get((item['station'],iso(nominal)))!=(offset,item['time']):
                continue
            key = item['station'], iso(nominal), item['var']
            candidate = {**item, 'report_time': item['time'], 'time': iso(nominal), 'offset_seconds': offset}
            previous = result.get(key)
            if previous is None or (offset, item['time']) < (previous['offset_seconds'], previous['report_time']):
                result[key] = candidate
        else:
            if at.minute or at.second or at.hour % 3:
                continue
            key = item['station'], item['time'], item['var']
            if key not in result or item['source'] == 'ogimet':
                result[key] = item
    return result


def load_observations(root, stations, end):
    cutoff = end - timedelta(days=31); ids = {s['id'] for s in stations}
    og, reports, unknown, diagnostics = ogimet_rows(root / 'raw', ids, cutoff, end)
    gh, ghdiag = ghcnh_rows(root / 'raw', stations, cutoff, end)
    metar = iem_rows(root / 'raw', ids, cutoff, end)
    rows = qc_rows(deduplicate(og + gh + metar))
    # Canonical source records include raw groups and all observation QC flags.
    with gzip.open(root / 'synop-decoded.jsonl.gz', 'wt') as handle:
        for report in reports:
            handle.write(json.dumps(report, ensure_ascii=False) + '\n')
    by_day = defaultdict(list)
    for item in rows:
        by_day[item['time'][:10]].append(item)
    out = root / 'obs'; out.mkdir(exist_ok=True)
    for day, items in by_day.items():
        path = out / (day + '.jsonl.gz'); temporary = path.with_suffix('.next')
        with gzip.open(temporary, 'wt') as handle:
            for item in items:
                handle.write(json.dumps(item, separators=(',', ':')) + '\n')
        temporary.replace(path)
    summary = {}
    last30 = [r for r in rows if r['time'] >= iso(end - timedelta(days=30))]
    for source in ('ogimet', 'ghcnh', 'iem'):
        items = [r for r in last30 if r['source'] == source]
        summary[source] = dict(latest=max((r['time'] for r in items), default=None),
                               stations=len({r['station'] for r in items}),
                               variables={v: dict(total=sum(r['var'] == v for r in items),
                               accepted=sum(r['var'] == v and not r['flags'] for r in items)) for v in VARIABLES})
    atomic_json(root / 'obs-status.json', dict(sources=summary, decoder=diagnostics,
                                              ghcnh=ghdiag, unknown_precip_periods=len(unknown)))
    comparison = agreement(rows, unknown, root)
    return select_observations(rows, stations), summary, comparison
