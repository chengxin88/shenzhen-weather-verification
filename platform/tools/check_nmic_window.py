#!/usr/bin/env python3
"""Inspect one published snapshot read-only, without raw data or recomputation."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import station_api as api


def inspect(root, days=(7,), element='rain', day=1):
    root = Path(root).resolve()
    current = json.loads((root/'current.json').read_text())
    generation = current['generation']
    if not re.fullmatch(r'\d{8}T\d{6}(?:\d{6})?', generation):
        raise ValueError('非法快照版本')
    directory = root/'snapshots'/generation
    metadata = api.cached_json(str(directory/'meta.json'))
    stations = metadata.get('stations', [])
    return dict(cache=str(root), generation=generation, window_end=metadata['window_end'],
                nmic_end=metadata.get('nmic_end'),
                regions={region:dict(nmic_majority=api.nmic_majority(region,stations),
                                     effective_window_end=api.iso(api.effective_window_end(metadata,region)))
                         for region in api.OVERVIEW_REGIONS},
                overview=[{k:result.get(k) for k in ('region','element','day','days','window_start','window_end',
                                                    'start','end','station_count','data_end_reason')}
                          for region in ('hn','gba','all') for days_count in days
                          for result in [api.overview_cached(str(directory),element,day,days_count,region)]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path, help='含 current.json 的站点缓存目录')
    parser.add_argument('--days', nargs='+', type=int, choices=(3,7,30), default=[7])
    parser.add_argument('--element', choices=('rain','t'), default='rain')
    parser.add_argument('--day', type=int, choices=(1,2,3), default=1)
    args = parser.parse_args()
    print(json.dumps(inspect(args.cache,args.days,args.element,args.day),ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
