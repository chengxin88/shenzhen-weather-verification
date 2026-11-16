"""Serve the frozen WSL history plus whatever Spark has scored since.

History before the move lives only in the frozen catalog, so a rebuild here must never shrink it.
Fresh rows replace frozen rows with the same identity and are appended otherwise; dataset ranges
are recomputed from the merged rows.
"""
import json
from pathlib import Path

_base = {}


def identity(row):
    return row.get('dataset'), row.get('model'), row.get('run'), row.get('lead'), row.get('threshold'), row.get('grade')


def load(path):
    stamp = Path(path).stat().st_mtime_ns
    if _base.get('stamp') != stamp:
        _base.update(stamp=stamp, value=json.loads(Path(path).read_bytes()))
    return _base['value']


def overlay(fresh, path):
    base = load(path)
    rows = {identity(r): r for r in base['rows']}
    rows.update({identity(r): r for r in fresh.get('rows', [])})
    merged = list(rows.values())

    datasets = {d['id']: dict(d) for d in base['datasets']}
    for item in fresh.get('datasets', []):
        datasets[item['id']] = {**datasets.get(item['id'], {}), **item}

    seen = {}
    for r in merged:
        s = seen.setdefault(r['dataset'], dict(models=set(), leads=set(), times=set(), thresholds=set()))
        s['models'].add(r['model'])
        s['leads'].add(r['lead'])
        s['times'].add(r['valid'])
        if r.get('threshold') is not None:
            s['thresholds'].add(r['threshold'])
    for ident, d in datasets.items():
        s = seen.get(ident)
        if not s:
            continue
        known = list(d.get('supported_models') or d.get('models') or [])
        d['models'] = [m for m in known if m in s['models']] + sorted(s['models'] - set(known))
        d['model_issues'] = {k: v for k, v in (d.get('model_issues') or {}).items() if k not in s['models']}
        d['leads'] = sorted(s['leads'])
        d['thresholds'] = sorted(s['thresholds'])
        d['start'], d['end'] = min(s['times']), max(s['times'])
        if 'times' in d:
            d['times'] = sorted(s['times'])

    value = {**base, **{k: v for k, v in fresh.items() if k not in ('rows', 'datasets') and v not in (None, [], {})}}
    value['rows'] = merged
    value['datasets'] = list(datasets.values())
    value['frozen_history_generated'] = base.get('generated')
    return value
