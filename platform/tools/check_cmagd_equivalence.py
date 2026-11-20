#!/usr/bin/env python3
"""只读对照：老站所有配对/明细/日统计、旧区域产品逐值核对，新站仅四模式。"""
import argparse
from contextlib import closing
from itertools import zip_longest
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from station_common import CMAGD_MODELS, DAY_WINDOWS, GRID_DATASETS, GRID_MODELS, iso
import station_api as api
from compare_aggregates import AggregateComparison, canonical, json_data, readonly, array_rows
from compare_station_runs import snapshot


def compare(a, b, api_checks=True):
    sa,sb=snapshot(a.resolve()),snapshot(b.resolve())
    ma,mb=(json_data(s/'meta.json') for s in (sa,sb))
    if ma['window_end']!=mb['window_end']: raise ValueError('必须冻结相同评分截止时刻')
    result=AggregateComparison(); ids={s['id'] for s in ma['stations']}
    extended={s['id'] for s in mb['stations'] if s.get('cmagd_extended')}
    result.value(sorted(ids), sorted(s['id'] for s in mb['stations'] if not s.get('cmagd_extended')), 'legacy_roster')
    def table(left,right,name,order,station_col,exact=False,json_col=None):
        with closing(readonly(left)) as da,closing(readonly(right)) as db:
            ca=da.execute('SELECT * FROM '+name+' ORDER BY '+order)
            cb=db.execute('SELECT * FROM '+name+' ORDER BY '+order)
            names=[c[0] for c in ca.description];si=names.index(station_col)
            ji=names.index(json_col) if json_col else None
            def legacy(cursor):
                for row in cursor:
                    if row[si] in ids: yield row
            count=0
            for count,(x,y) in enumerate(zip_longest(legacy(ca),legacy(cb)),1):
                at=f'{name}[{count}]'
                if exact:
                    if x!=y: result.fail(at,x,y)
                elif x!=y:
                    if x is None or y is None: result.fail(at,x,y);continue
                    x,y=dict(zip(names,x)),dict(zip(names,y))
                    if ji is not None:
                        x[json_col],y[json_col]=json.loads(x[json_col]),json.loads(y[json_col])
                    result.value(x,y,at)
            result.checked[name]=count
            print('CMAGD_CHECK',name,count,'rows',result.mismatches,'mismatches',flush=True)
    table(sa/'pairs.sqlite',sb/'pairs.sqlite','pairs','model,run,lead,station,var','station',exact=True)
    table(sa/'pairs.sqlite',sb/'pairs.sqlite','detail_stats',
          "station,model,var,lead,days,threshold,COALESCE(json_extract(stats,'$.grade'),0)",'station',json_col='stats')
    table(sa/'grid-map.sqlite',sb/'grid-map.sqlite','daily','model,var,day,lead,station','station')
    def arrays(name,station_filter=False):
        # Full-scale station JSONs can exceed a million rows. Bound comparison
        # memory with a disposable disk sort beside the private candidate.
        with tempfile.TemporaryDirectory(prefix='cmagd-sort-',dir=b) as folder, \
             closing(sqlite3.connect(Path(folder)/'sort.sqlite')) as db:
            db.execute('PRAGMA cache_size=-8000');db.execute('PRAGMA temp_store=FILE')
            for side,path in (('a',sa/name),('b',sb/name)):
                db.execute(f'CREATE TABLE {side} (k TEXT,v TEXT)')
                def records():
                    for row in array_rows(path):
                        if station_filter and row['station'] not in ids:continue
                        row={k:v for k,v in row.items() if k not in ('created','updated')}
                        key={k:v for k,v in row.items() if k in ('station','model','var','lead','lead_min','lead_max','threshold','grade','run','valid','dataset')}
                        yield json.dumps(key,sort_keys=True),json.dumps(row,separators=(',',':'))
                db.executemany(f'INSERT INTO {side} VALUES (?,?)',records())
                db.execute(f'CREATE INDEX {side}_key ON {side}(k)')
            count=0
            for count,(x,y) in enumerate(zip_longest(db.execute('SELECT v FROM a ORDER BY k'),db.execute('SELECT v FROM b ORDER BY k')),1):
                if x is None or y is None:result.fail(name+'.length',x,y)
                elif x!=y:result.value(json.loads(x[0]),json.loads(y[0]),f'{name}[{count}]')
            result.checked[name]=count
            print('CMAGD_CHECK',name,count,'rows',result.mismatches,'mismatches',flush=True)
    for days in DAY_WINDOWS:
        for prefix in ('scores','station-scores'):
            arrays(f'{prefix}-{days}.json',prefix=='station-scores')
    for variable in GRID_DATASETS:
        arrays(f'grid-rows-{variable}.json.gz')
    result.value(json_data(sa/'common-report.json'),json_data(sb/'common-report.json'),'common-report.json')
    datasets=[]
    for s in (sa,sb):
        rows=json_data(s/'grid-datasets.json')
        for r in rows:
            if 'region_nmic_majority' in r:r['region_nmic_majority'].pop('cmagd',None)
            r.pop('cmagd_nmic_end',None)
        datasets.append(canonical(rows,('created','updated','gzip_bytes')))
    result.value(*datasets,'grid-datasets.json')
    new_models={};unexpected=0
    with closing(readonly(sb/'pairs.sqlite')) as db:
        for station,model,count in db.execute('SELECT station,model,COUNT(*) FROM pairs GROUP BY station,model'):
            if station in extended:
                new_models[model]=new_models.get(model,0)+count
                if model not in CMAGD_MODELS: unexpected+=count
    result.value(0,unexpected,'new_station_forbidden_pairs')
    regions=[r for r in api.OVERVIEW_REGIONS if r!='cmagd']
    for region in regions:
        result.value(sorted(api.region_stations(region,ma['stations'])),
                     sorted(api.region_stations(region,mb['stations'])),region+'.stations')
        result.value(iso(api.effective_window_end(ma,region)),iso(api.effective_window_end(mb,region)),region+'.end')
        if api_checks:
            for element in ('t','rain'):
                for day in (1,2,3):
                    for days in (3,7,15):
                        x,y=(api.overview_cached(str(s),element,day,days,region) for s in (sa,sb))
                        x,y=({k:v for k,v in d.items() if k!='generation'} for d in (x,y))
                        result.value(x,y,f'overview.{region}.{element}.{day}.{days}')
            for variable, dataset in GRID_DATASETS.items():
                rows=[]
                for s,metadata in ((sa,ma),(sb,mb)):
                    params=dict(dataset=dataset,start='2000-01-01',end=ma['window_end'][:10],region=region)
                    with patch.object(api,'grid_rows_file',return_value=s/f'grid-rows-{variable}.json.gz'), \
                         patch.object(api,'meta',return_value=metadata):
                        rows.append(canonical(api.grid_rows(params),('created','updated')))
                result.value(*rows,f'grid.{region}.{variable}')
    if api_checks:
        for element in ('t','rain'):
            data=api.overview_cached(str(sb),element,1,7,'cmagd')
            result.value([], sorted({r['model'] for r in data['rows']}-set(CMAGD_MODELS)), 'cmagd.overview.models')
    return dict(equal=result.mismatches==0,mismatches=result.mismatches,errors=result.errors,
                checked=result.checked,max_relative_error=result.max_relative_error,
                float_differences=result.float_differences,old_stations=len(ids),extended_stations=len(extended),
                new_pairs_by_model=new_models,forbidden_new_pairs=unexpected,
                cmagd_nmic_majority=api.nmic_majority('cmagd',mb['stations']),
                cmagd_window_end=iso(api.effective_window_end(mb,'cmagd')),
                tolerance='计数精确相等；配对全部列精确相等；连续汇总相对误差 <= 1e-9（无绝对误差豁免）',
                api_checks=api_checks)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline',type=Path);p.add_argument('candidate',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--skip-api',action='store_true')
    a=p.parse_args();result=compare(a.baseline,a.candidate,not a.skip_api)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['equal'] else 1)
