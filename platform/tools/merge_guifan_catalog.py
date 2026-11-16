#!/usr/bin/env python3
"""Build a frozen preview from an exported catalog and freshly scored local artifacts."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from spark_overlay import overlay


def merge(base,native,openmeteo):
    datasets={d['id']:dict(d) for d in base['datasets']}
    datasets.update({d['id']:d for d in native['datasets']})
    rows=list(native['rows'])
    # City datasets keep their own IDs. Analysis datasets use their original
    # published aliases, matching the main catalog without loading any adapters.
    aliases={}
    for d in openmeteo['datasets']:
        target=None
        if d['id'].startswith('openmeteo-analysis-wide-'):
            _,_,_,source,element=d['id'].split('-')
            candidate=datasets.get(f'analysis-{source}-{element}')
            if candidate and all(candidate.get(k)==d.get(k) for k in ('kind','reference','region','element','height_m','unit')):target=candidate
        if target is None:datasets[d['id']]=d;aliases[d['id']]=d['id']
        else:
            aliases[d['id']]=target['id']
            for key in ('models','supported_models'):target[key]=list(dict.fromkeys(target.get(key,[])+d.get(key,[])))
            target['model_sources']={**target.get('model_sources',{}),**d.get('model_sources',{})}
    rows.extend(dict(r,dataset=aliases[r['dataset']],source_dataset=r['dataset']) for r in openmeteo['rows'])
    return dict(datasets=list(datasets.values()),rows=rows)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base','native','openmeteo','output'):p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args()
    fresh=merge(*(json.loads(path.read_text()) for path in (a.base,a.native,a.openmeteo)))
    value=overlay(fresh,a.base)
    a.output.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False))
    print('PASS frozen preview catalog',len(value['rows']),'rows')
