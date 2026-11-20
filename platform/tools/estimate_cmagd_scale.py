#!/usr/bin/env python3
"""用实际配对器枚举完整时效/起报周期，估算扩展站配对上界（无缺测）。"""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from station_common import CMAGD_MODELS, PAIR_DAYS, RUN_DAYS, iso, stamp
from station_score import pair_run
from station_obs import row


def capacity(end, first=None):
    cutoff=end-timedelta(days=PAIR_DAYS);first=first or cutoff
    observations={};at=first
    while at<=end:
        for var in ('t','td','msl','wind','rain6','rain12','rain24'):
            if first>cutoff and var.startswith('rain') and at-first<timedelta(hours=int(var[4:])-3):continue
            observations['x',iso(at),var]=row('x',iso(at),var,1.,'nmic')
        at+=timedelta(hours=3)
    counts={};station=dict(id='x',sources=['nmic'],cmagd_extended=True)
    for model in CMAGD_MODELS:
        leads=(list(range(0,145,3))+list(range(150,241,6)) if model=='ecmwf' else
               list(range(0,241,3)) if model=='gfs' else list(range(0,241,6)))
        samples={h:{v:np.array([1.]) for v in ('t','td','msl','wind')} for h in leads}
        for h in leads:
            if h:
                step=3 if model=='gfs' or (model=='ecmwf' and h<=144) else 6
                samples[h].update(rain=np.array([1.]),rain_start=h-step)
        run=(end-timedelta(days=RUN_DAYS)).replace(hour=0,minute=0,second=0,microsecond=0);n=0
        while run<=end:
            n+=sum(1 for _ in pair_run(model,run,samples,observations,[station],cutoff,end))
            run+=timedelta(hours=6)
        counts[model]=n
    return counts


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expanded-stations',type=Path,required=True)
    p.add_argument('--end',default='2026-11-16T00:00:00Z')
    p.add_argument('--first',default='2026-11-04T06:00:00Z')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();n=sum(bool(s.get('cmagd_extended')) for s in json.loads(a.expanded_stations.read_text())['stations'])
    steady=capacity(stamp(a.end));first=capacity(stamp(a.end),stamp(a.first))
    result=dict(extended=n,pair_days=PAIR_DAYS,run_days=RUN_DAYS,
                assumptions='每天四个起报、完整 240 小时时效、全部要素可用；逐个配对器枚举，实际缺帧和缺测只会减少。首轮计入新资料起点造成的累计降水缺口。',
                per_station=dict(steady=steady,first=first),
                added=dict(steady=sum(steady.values())*n,first=sum(first.values())*n),
                conservative_1657=dict(steady=sum(steady.values())*1657,first=sum(first.values())*1657),
                steady_legacy_assumed_range=[85000000,100000000])
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
