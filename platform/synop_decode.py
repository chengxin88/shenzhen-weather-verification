"""Strict, deliberately partial FM-12 land SYNOP decoder (sections 1 and 333).

Unknown precipitation periods remain unknown. No hour-dependent inference is
made for omitted groups. Raw groups and unsupported groups remain auditable.
"""
import csv
import re
from station_common import UTC, iso
from datetime import datetime

PERIODS = {1: 6, 2: 12, 3: 18, 4: 24, 5: 1, 6: 2, 7: 3, 8: 9, 9: 15}


def number(text):
    return int(text) if text.isdigit() else None


def temperature(group):
    if len(group) != 5 or group[1] not in '01' or not group[2:].isdigit():
        return None
    return (-1 if group[1] == '1' else 1) * int(group[2:]) / 10


def pressure(group):
    if len(group) != 5 or not group[1:].isdigit():
        return None
    value = int(group[1:]) / 10
    return value + (1000 if value < 500 else 0)


def precipitation(group):
    """Decode 6RRRtR without assuming that an omitted/zero tR is six hours."""
    amount = number(group[1:4])
    period = PERIODS.get(number(group[4:5]))
    result = {'value': None, 'hours': period, 'trace': False,
              'lower_bound': False, 'group': group}
    if amount is None:
        return result
    if amount <= 988:
        result['value'] = float(amount)
    elif amount == 989:
        result.update(value=989., lower_bound=True)
    elif amount == 990:
        result.update(value=0., trace=True)
    else:
        result['value'] = (amount - 990) / 10
    return result


def decode(line):
    """Return one report or None for comments/empty lines; invalid dates raise."""
    if not re.match(r'^\d{5},', line.strip()):
        return None
    cells = next(csv.reader([line.strip()]))
    if len(cells) < 7:
        raise ValueError('Truncated OGIMET record')
    station = cells[0]
    at = datetime(*(int(x) for x in cells[1:6]), tzinfo=UTC)
    text = ','.join(cells[6:]).strip().rstrip('=')
    tokens = text.split()
    correction = next((x for x in tokens if re.fullmatch(r'CC[A-Z]', x)), '')
    report = {'station': station, 'time': iso(at), 'source': 'ogimet', 'raw': text,
              'correction': correction, 'nil': 'NIL' in tokens, 'values': {},
              'precip': [], 'groups333': [], 'flags': []}
    if report['nil']:
        return report
    tokens = [x for x in tokens if not re.fullmatch(r'CC[A-Z]', x)]
    # The first station ID after AAXX/YYGGiw anchors the two mandatory groups.
    try:
        start = tokens.index('AAXX')
        clock = tokens[start + 1]
        idx = start + 2
        if tokens[idx]!=station:
            raise ValueError('SYNOP station identity mismatch')
    except (ValueError, IndexError):
        raise ValueError('Missing SYNOP header') from None
    if not re.fullmatch(r'\d{4}[0134/]', clock) or clock[:2] != at.strftime('%d') or clock[2:4] != at.strftime('%H'):
        raise ValueError('SYNOP clock disagrees with OGIMET timestamp')
    iw = number(clock[-1]); groups = tokens[idx + 1:]
    if len(groups) < 2 or len(groups[0]) != 5 or len(groups[1]) != 5:
        raise ValueError('Missing mandatory SYNOP groups')
    irix, wind = groups[:2]; report['iR'] = number(irix[0]); report['iw'] = iw
    report['visibility_group'] = irix; report['cloud_wind_group'] = wind
    report.update(iX=number(irix[1]),cloud_base_code=number(irix[2]),visibility_code=number(irix[3:]),
                  total_cloud_code=number(wind[0]))
    speed = number(wind[3:5]); direction = number(wind[1:3])
    remaining = groups[2:]
    if speed == 99:
        extended = next((g for g in remaining[:1] if re.fullmatch(r'00\d{3}', g)), None)
        speed = int(extended[2:]) if extended else None
        if extended:
            remaining = remaining[1:]
    values = report['values']
    if speed is not None and iw in (0, 1, 3, 4):
        values['wind'] = speed * (.5144444444444445 if iw in (3, 4) else 1)
    if direction is not None and 0 <= direction <= 36:
        values['direction'] = direction * 10
    section = 1; radiation = False
    for group in remaining:
        if group in ('222', '333', '444', '555') or group.startswith('222'):
            section = 333 if group == '333' else 0
            continue
        if len(group) != 5:
            continue
        if section == 333:
            report['groups333'].append(group)
            # 55SSS/553SS may be followed by radiation groups 0FFFF..6FFFF; a 6FFFF
            # there is not 6RRRtR and a 2FFFF is not Tmin. The block ends at the
            # next 5-group (pressure change) or a 7/8/9 group.
            if group.startswith('55'):
                radiation = True; continue
            if radiation and group[0] in '012346':
                report.setdefault('radiation_groups', []).append(group); continue
            radiation = False
            if group[0] in '12':
                report['tmax' if group[0] == '1' else 'tmin'] = temperature(group)
            elif group[0] == '6':
                report['precip'].append({**precipitation(group), 'section': 333})
            elif group[0] == '7':
                n = number(group[1:])
                report['precip'].append({'value': None if n is None else 0. if n == 9999 else n / 10,
                                        'hours': 24, 'trace': n == 9999,
                                        'lower_bound': False, 'group': group, 'section': 333})
            elif group[0]=='3':
                report['ground_group333']={'state_code':number(group[1]),'payload':group[2:],'group':group}
            continue
        if section != 1:
            continue
        if group[0] == '1':
            values['t'] = temperature(group)
        elif group[0] == '2':
            if group[1] == '9':
                rh = number(group[2:]); values['rh'] = rh if rh is not None and rh <= 100 else None
            else:
                values['td'] = temperature(group)
        elif group[0] == '3':
            values['station_pressure'] = pressure(group)
        elif group[0] == '4':
            # a3=0/1/2/5/7/8 is geopotential of a standard surface, not SLP.
            if group[1] in '09':
                values['msl'] = pressure(group)
            else:
                report['geopotential_group'] = group
        elif group[0] == '5':
            report['pressure_tendency_group'] = group
            magnitude=number(group[2:])
            report['pressure_tendency']={'characteristic_code':number(group[1]),
                                         'magnitude_hpa':None if magnitude is None else magnitude/10}
        elif group[0] == '6':
            report['precip'].append({**precipitation(group), 'section': 1})
        elif group[0] == '7':
            report['weather_group'] = group
            report['weather_codes']={'present':number(group[1:3]),'past1':number(group[3]),'past2':number(group[4])}
        elif group[0] == '8':
            report['cloud_group'] = group
            report['cloud_codes']={'amount':number(group[1]),'low':number(group[2]),'middle':number(group[3]),'high':number(group[4])}
    if report['iR'] == 3 and not any(p['section']==1 for p in report['precip']):
        report['precip'].append({'value': 0., 'hours': None, 'trace': False,
                                'lower_bound': False, 'group': 'iR=3', 'section': 1})
    if report['iR'] == 4:
        report['unobserved_precip_groups']=report['precip']
        report['precip']=[]
    if values.get('td') is None and values.get('t') is not None and values.get('rh') not in (None, 0):
        import math
        t, rh = values['t'], values['rh']
        g = math.log(rh / 100) + 17.625 * t / (243.04 + t)
        values['td'] = 243.04 * g / (17.625 - g)
        report['dewpoint_derived_from_rh'] = True
    return report


def latest_reports(lines):
    """CCA < CCB; equal correction rank resolves to the last received line."""
    result = {}
    for line in lines:
        report = decode(line)
        if report is None:
            continue
        key = report['station'], report['time']
        if key not in result or report['correction'] >= result[key]['correction']:
            result[key] = report
    return list(result.values())
