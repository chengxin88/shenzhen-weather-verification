"""Lossless recovery of legacy native rain intervals from rounded NPZ caches.

Only source values with identical float32 encodings are candidates. Published
forecast totals constrain which original interval precision was used; observed
values never enter the recovery. The usual 3/6h native tiling gives at most eight
binary choices for a 24h total. No score or pair row is modified here.
"""
from collections import defaultdict
from datetime import timedelta
from itertools import product
from pathlib import Path
import numpy as np
from station_common import stamp
from station_forecasts import accumulate,interpolate,native,NATIVE


def legacy_leads(sources):
    return {int(h) for h,sig in sources
            if isinstance(sig,list) and len(sig)==3 and isinstance(sig[0],str)
            and sig[0].endswith('.json') and not sig[0].endswith('_global1.json')}


def restore_rain(samples, sources, metadata, model, run_text, stations, rows):
    """Restore interval arrays only when every existing 6/24h fc is exact."""
    indices={s['id']:i for i,s in enumerate(stations)}
    groups=defaultdict(list)
    for lead,station,var,fc in rows:
        if station in indices:groups[lead,int(var[4:])].append((indices[station],fc))
    if not groups:return 0
    intervals={h:(float(s['rain_start']),np.asarray(s['rain'],dtype=float)) for h,s in samples.items()
               if 'rain' in s and 'rain_start' in s and np.isfinite(s['rain_start'])}
    constraints=[]
    for (lead,hours),records in groups.items():
        target=lead-hours;cursor=lead;tiles=[]
        if target<0:raise ValueError('Published pre-run rainfall total')
        while cursor>target:
            if cursor not in intervals:raise ValueError(f'Missing legacy rain interval: {model} {run_text} {cursor}')
            start,_=intervals[cursor]
            if not target<=start<cursor:raise ValueError('Invalid legacy rain tiling')
            tiles.append(cursor);cursor=start
        selected=np.array([r[0] for r in records]);expected=np.array([r[1] for r in records])
        constraints.append((lead,hours,tiles,selected,expected))
    if all(np.array_equal(accumulate(intervals,h,w)[idx],fc) for h,w,_,idx,fc in constraints):return 0
    source_map=dict(sources);candidates={};run=stamp(run_text)
    targets=[(s['lon'],s['lat']) for s in stations] if all('lon' in s and 'lat' in s for s in stations) else None
    for lead in legacy_leads(sources):
        if lead not in intervals:continue
        current=intervals[lead][1];raw=None;sig=source_map[lead]
        entry=metadata.get(str(lead),{})
        if targets is not None and entry.get('path')==sig[0] and entry.get('meta_stat')==sig[1]:
            data=entry.get('metadata',{})
            try:
                x,y=native().validate_frame(data,NATIVE[model],run,lead)
                if data.get('rain3h') is not None and stamp(data['rain_start'])==run+timedelta(hours=lead-3):
                    raw=interpolate(np.asarray(data['rain3h'],float).reshape(len(y),len(x)),x,y,targets,nearest=True)
            except (ValueError,KeyError,TypeError):pass
        if raw is None:
            # The legacy regional JSON contract stores three decimal places.
            # This fallback is admitted only by exact float32 and total checks.
            raw=np.round(current,3)
        equivalent=(raw.astype(np.float32)==current.astype(np.float32)) | (np.isnan(raw)&np.isnan(current))
        if np.all(equivalent) and not np.array_equal(raw,current,equal_nan=True):candidates[lead]=raw
    clauses=[]
    for lead,hours,tiles,selected,expected in constraints:
        variables=tuple(h for h in tiles if h in candidates)
        if len(variables)>8:raise ValueError('Unsupported legacy rain interval count')
        allowed=[]
        for bits in product((0,1),repeat=len(variables)):
            trial=dict(intervals)
            for h,bit in zip(variables,bits):
                if bit:trial[h]=(intervals[h][0],candidates[h])
            if np.array_equal(accumulate(trial,lead,hours)[selected],expected):allowed.append(bits)
        if not allowed:raise ValueError(f'Legacy rain cache cannot reproduce published fc: {model} {run_text} {lead}/{hours}')
        if variables:clauses.append((variables,allowed))

    def solve(assignments):
        assignments=dict(assignments)
        while True:
            changed=False;remaining=[]
            for variables,allowed in clauses:
                options=[bits for bits in allowed if all(h not in assignments or assignments[h]==bit for h,bit in zip(variables,bits))]
                if not options:return None
                remaining.append((variables,options))
                for i,h in enumerate(variables):
                    possible={bits[i] for bits in options}
                    if len(possible)==1 and h not in assignments:
                        assignments[h]=possible.pop();changed=True
            if not changed:break
        unassigned={h for variables,_ in remaining for h in variables if h not in assignments}
        if not unassigned:return assignments
        h=max(unassigned,key=lambda h:sum(h in variables for variables,_ in remaining))
        for bit in (0,1):
            result=solve({**assignments,h:bit})
            if result is not None:return result
        return None

    chosen=solve({})
    if chosen is None:raise ValueError(f'Inconsistent legacy rainfall precision: {model} {run_text}')
    restored=0
    for lead,bit in chosen.items():
        if bit:
            restored+=int(np.count_nonzero(intervals[lead][1]!=candidates[lead]))
            samples[lead]['rain']=candidates[lead]
            intervals[lead]=(intervals[lead][0],candidates[lead])
    if not all(np.array_equal(accumulate(intervals,h,w)[idx],fc) for h,w,_,idx,fc in constraints):
        raise ValueError('Legacy rainfall precision verification failed')
    return restored
