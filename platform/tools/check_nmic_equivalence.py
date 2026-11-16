#!/usr/bin/env python3
"""Exact old-station pair/score comparison between two frozen cache snapshots.

Usage: python tools/check_nmic_equivalence.py BASE_CACHE NMIC_CACHE
Exit 0 requires identical old-station population and scores. Added pairs in old
gaps are reported separately, never silently called equivalent. Inputs read-only.
"""
import argparse
import json
from pathlib import Path
import sqlite3


def snapshot(root):
    pointer = json.loads((root/'current.json').read_text())
    return root/'snapshots'/pointer['generation']


def pairs(path, ids):
    with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        for r in db.execute('SELECT * FROM pairs ORDER BY model,run,lead,station,var'):
            if r[4] in ids:
                yield (r[0],r[1],r[2],r[4],r[5]),r


def compare(a, b):
    left, right = snapshot(a.resolve()), snapshot(b.resolve())
    ma,mb=[json.loads((p/'meta.json').read_text()) for p in (left,right)]
    if ma['window_end'] != mb['window_end']:
        raise ValueError('Different scoring end times; freeze the same inputs/end first')
    ids = {s['id'] for s in ma['stations']}
    counts = dict(before=0,after=0,unchanged=0,modified=0,removed=0,added=0)
    examples=[];ia=iter(pairs(left/'pairs.sqlite',ids));ib=iter(pairs(right/'pairs.sqlite',ids))
    x,y=next(ia,None),next(ib,None)
    while x is not None or y is not None:
        if y is None or (x is not None and x[0]<y[0]):
            counts['removed']+=1;counts['before']+=1;kind='removed';key=x[0];x=next(ia,None)
        elif x is None or y[0]<x[0]:
            counts['added']+=1;counts['after']+=1;kind='added';key=y[0];y=next(ib,None)
        else:
            kind='unchanged' if x[1]==y[1] else 'modified';key=x[0]
            counts[kind]+=1;counts['before']+=1;counts['after']+=1;x,y=next(ia,None),next(ib,None)
        if kind!='unchanged' and len(examples)<20:examples.append(dict(kind=kind,key=key))
    scores={}
    for days in (1,3,7,30):
        name=f'station-scores-{days}.json'
        def read(folder):
            rows=json.loads((folder/name).read_text())
            return sorted((r for r in rows if r['station'] in ids),key=lambda r:json.dumps(r,sort_keys=True))
        l,r=read(left),read(right)
        scores[str(days)]=dict(equal=l==r,before=len(l),after=len(r))
    preserved=counts['modified']==counts['removed']==0
    equal=preserved and counts['added']==0 and all(s['equal'] for s in scores.values())
    return dict(equal=equal,existing_pairs_preserved=preserved,old_stations=len(ids),window_end=ma['window_end'],
                pairs=counts,station_scores=scores,examples=examples,
                policy='exact floats, all pair columns; no tolerance, no ignored old-gap pairs')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline',type=Path);parser.add_argument('candidate',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=compare(args.baseline,args.candidate)
    text=json.dumps(result,ensure_ascii=False,indent=2);print(text)
    if args.output:args.output.write_text(text+'\n')
    raise SystemExit(0 if result['equal'] else 1)
