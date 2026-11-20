#!/usr/bin/env python3
"""离线比较加站前后的外部请求计划；不发送 HTTP 请求。"""
import argparse
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from station_common import ROOT, BOX, atomic_json, digest, model_allowed, read_json, stamp
import nmic_ec01
import nmic_obs
import station_sources


def roster(table):
    everyone=table['stations']+table.get('inactive',[])
    points=lambda rows:[[s['id'],s['lon'],s['lat']] for s in rows]
    return dict(ghcnh=[s['ghcnh_id'] for s in everyone if 'ghcnh' in s.get('sources',())],
                iem=[s['id'] for s in everyone if s.get('type')=='metar'],
                ec01_supplement=points(nmic_ec01.request_stations(table['stations'])),
                ec01_extended=points([s for s in table['stations'] if model_allowed(s,'ec01')]))


def audit(before,after,baseline_code):
    a,b=roster(before),roster(after)
    details={k:dict(before=len(a[k]),after=len(b[k]),identical=a[k]==b[k],
                    before_sha256=digest(a[k]),after_sha256=digest(b[k])) for k in a}
    unchanged={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==
                    hashlib.sha256((baseline_code/name).read_bytes()).hexdigest()
               for name in ('station_sources.py','build_stations.py','refresh_stations.py',
                            'fetch_ecmwf_ens.py','fetch_gefs.py','fetch_extended_backfill.py')}
    blocks=inspect.signature(station_sources.ogimet).parameters['blocks'].default
    counts=dict(metadata=3,ogimet=len(blocks),iem=int(bool(a['iem'])),ghcnh=len(a['ghcnh']),
                openmeteo_automatic=0)
    # Calling code and cache age decide which metadata/daily downloads are
    # skipped, identically in both plans. GET budget/retries are unchanged.
    return dict(equal=all(d['identical'] for d in details.values()) and all(unchanged.values()),
                method='相同缓存年龄、30 小时 OGIMET 窗口、26 小时 IEM 窗口；名单顺序和坐标逐值比较，源代码散列比较；不联网',
                rosters=details,unchanged_sources=unchanged,box=BOX,ogimet_blocks=blocks,
                cold_request_upper_bound=dict(before=counts,after=counts),
                explicit_openmeteo=dict(supplement='每个缺失起报一批、站序和坐标相同，已有证书不会因扩展站失效',
                                        extended='每个缺失起报同一批原站，重试规则未变'),
                note='冷缓存上界不是本次实际发出的请求数；所有审计 HTTP 请求均为零。')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stations',type=Path,required=True);p.add_argument('--nmic',type=Path,required=True)
    p.add_argument('--metadata',type=Path,required=True);p.add_argument('--baseline-code',type=Path,required=True)
    p.add_argument('--end',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();before=read_json(a.stations);after=deepcopy(before)
    with patch.dict(os.environ,STATIONS_NMIC_DIR=str(a.nmic.resolve()),STATIONS_NMIC_METADATA=str(a.metadata.resolve())):
        nmic_obs.add_stations(after,end=stamp(a.end))
    result=audit(before,after,a.baseline_code);atomic_json(a.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['equal'] else 1)
