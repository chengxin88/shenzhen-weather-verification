#!/usr/bin/env python3
"""本机真实样本重放：冻结的实况 JSONL + 本地区域帧，不联网、不修改输入。"""
import argparse
from datetime import timedelta
import gzip
import json
import os
from pathlib import Path
import resource
import socket
import sqlite3
import sys
from unittest.mock import patch


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--code',type=Path,required=True)
    p.add_argument('--stations',type=Path,required=True)
    p.add_argument('--obs',type=Path,required=True)
    p.add_argument('--frames',type=Path,required=True)
    p.add_argument('--nmic',type=Path,required=True)
    p.add_argument('--metadata',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--end',default='2026-11-05T06:55:00Z')
    p.add_argument('--resume',action='store_true',help='仅用于补测同一组冻结输入；复用私有配对库')
    p.add_argument('--check-pairs-only',action='store_true',help='最终代码重新生成全部配对，与已重放私有库逐列精确核对；不写缓存')
    a=p.parse_args()
    if a.output.exists() and not (a.resume or a.check_pairs_only):p.error('输出必须是新目录，或明确指定 --resume')
    sys.path.insert(0,str(a.code.resolve()))
    os.environ['STATIONS_NMIC_DIR']=str(a.nmic.resolve())
    os.environ['STATIONS_NMIC_METADATA']=str(a.metadata.resolve())
    os.environ['OPENBLAS_NUM_THREADS']='1'
    def offline(*args,**kwargs):raise RuntimeError('离线对照禁止联网')
    socket.socket.connect=offline;socket.create_connection=offline
    from station_common import atomic_json, iso, stamp
    import station_score as score
    import station_forecasts as forecasts
    import nmic_obs
    from station_obs import qc_rows,select_observations
    # The baseline lacks CMAGD constants; literal four source slugs below are
    # the agreed input inventory and are independent of its pairing logic.
    if not a.check_pairs_only:
        a.output.mkdir(exist_ok=a.resume);atomic_json(a.output/'stations.json',json.loads(a.stations.read_text()))
    legacy=[]
    for path in sorted(a.obs.glob('*.jsonl.gz')):
        with gzip.open(path,'rt') as f:legacy.extend(json.loads(line) for line in f)
    end=stamp(a.end)
    def observations(root,stations,end,archive_only=()):
        ids={s['id'] for s in stations}
        before=[r for r in legacy if r['station'] in ids and stamp(r['time'])<=end]
        extra=qc_rows(nmic_obs.observation_rows(stations,end,end-timedelta(days=16),root))
        return select_observations(before+extra,stations),{},{}
    def runs(root,stations,end,*args,**kwargs):
        points=[(s['lon'],s['lat']) for s in stations]
        for model in ('ecmwf','gfs','aifs','aigfs'):
            samples={};run=None
            for path in sorted((a.frames/model).glob('*.json')):
                data=json.loads(path.read_text());run=stamp(data['run']);h=data['lead']
                samples[h]=forecasts.read_native_frame(path,model,run,h,points,data)
            if samples:yield model,run,samples,'real-frozen-'+model,[]
    if a.check_pairs_only:
        from contextlib import closing
        from itertools import zip_longest
        import sz_district_obs
        table=json.loads(a.stations.read_text());nmic_obs.add_stations(table,a.output,end);sz_district_obs.add_stations(table)
        stations=table['stations'];selected=observations(a.output,stations,end)[0];checked=errors=0;examples=[]
        with closing(sqlite3.connect((a.output/'working.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            for model,run,samples,signature,issues in runs(a.output,stations,end):
                expected=sorted(score.pair_run(model,run,samples,selected,stations,end-timedelta(days=16),end),key=lambda r:(r[2],r[4],r[5]))
                actual=db.execute('SELECT * FROM pairs WHERE model=? AND run=? ORDER BY lead,station,var',(model,iso(run)))
                for left,right in zip_longest(expected,actual):
                    checked+=1
                    if left!=right:
                        errors+=1
                        if len(examples)<10:examples.append(dict(expected=left,actual=right))
            total=db.execute('SELECT COUNT(*) FROM pairs').fetchone()[0]
        result=dict(equal=errors==0 and checked==total,checked_pairs=checked,total_pairs=total,mismatches=errors,examples=examples,
                    evidence='最终代码重新取样、选择真实实况并生成配对，与此前完整重放产物全部列精确相等')
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['equal'] else 1
    with patch.object(score,'load_observations',side_effect=observations), \
         patch.object(score,'forecast_runs',side_effect=runs), \
         patch.object(score,'shenzhen_sanity',return_value=dict(n=0)):
        result=score.refresh(a.output,end)
    result['peak_rss_mib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    atomic_json(a.output/'measurement.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':raise SystemExit(main())
