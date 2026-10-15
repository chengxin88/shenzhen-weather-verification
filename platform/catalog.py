"""Read the existing trusted verification cache without changing source data.

Metadata is admitted only after reconstructing and verifying the original cache
signature. No API credential or raw failure message is sent to the web client.
"""
import hashlib
import itertools
import json
import math
import os
import pickle
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP = Path(os.getenv('VERIFICATION_APP_ROOT', '/home/chengyang/ec-gfs-server'))
MODELS = ('ECMWF-IFS', 'ECAI', 'ECMWF-ENSEMBLES平均', 'NCEP-GFS', 'GFSAI', 'Google WeatherNext 2')
OLD_MODELS = MODELS
WN3 = "Google WeatherNext 3（集合均值）"
MODELS = MODELS + (WN3,)
GOOGLE = "Google WeatherNext 2"
BJT = timezone(timedelta(hours=8))

def fingerprint(parameters):
    return hashlib.sha256(json.dumps(parameters, ensure_ascii=True, sort_keys=True,
        separators=(',', ':'), default=str).encode()).hexdigest()

def metadata(path, result, key_hashes, parameters=None):
    weekly = isinstance(result, dict)
    frame = result['per_cycle'] if weekly else result[0]
    if frame.empty:
        return None
    kind = '降水' if '阈值(mm)' in frame else ('环流' if '高度RMSE(m)' in frame else '气温')
    thresholds = tuple(float(x) for x in frame['阈值(mm)'].drop_duplicates()) if kind == '降水' else ()
    dates = frame['有效时间（BJT）']
    if weekly:
        count = int(result['requested_cycles'])
        lead = int((result['end_valid_bjt'] - result['end_run_bjt']).total_seconds() / 3600)
        base = dict(verification_type=kind,
            end_run_utc=(result['end_run_bjt']-timedelta(hours=8)).isoformat(),
            lead_hour=lead, cycle_count=count, thresholds=thresholds)
    else:
        fails = result[1]
        leads = set(int(x) for x in frame['时效（小时）'])
        if '时效（小时）' in fails:
            leads.update(int(x) for x in fails['时效（小时）'].dropna())
        base = dict(verification_type=kind, valid_utc=(dates.iloc[0]-timedelta(hours=8)).isoformat(),
            lead_hours=tuple(sorted(leads)), thresholds=thresholds)
    if parameters is not None:
        if fingerprint(parameters)!=path.stem or parameters.get('verification_type')!=kind or not set(parameters.get('models',())).issubset(MODELS):
            return None
        return dict(kind=kind, accumulation=parameters['accumulation'],
                    level=parameters['circulation_level'] if kind=='环流' else None,
                    source='weekly' if weekly else 'lead', models=list(parameters['models']),
                    pilot=list(parameters['models'])==[WN3])
    periods = (3,6,12,24,1) if kind=='降水' else (None,)
    levels = (500,850,700,925,200,300)
    for accumulation, level, key_hash in itertools.product(periods, levels, key_hashes if kind!='环流' else [hashlib.sha256(b'').hexdigest()]):
        models = OLD_MODELS if kind!='环流' else OLD_MODELS[:-1]
        if kind=='降水' and (accumulation==3 or (weekly and (lead<24 or lead%24))):
            models = tuple(x for x in models if x!=GOOGLE)
        params = dict(base, accumulation=accumulation, circulation_level=level, models=models, app_key_sha256=key_hash)
        if fingerprint(params)==path.stem:
            return dict(kind=kind, accumulation=accumulation, level=level if kind=='环流' else None,
                source='weekly' if weekly else 'lead', models=list(models))
    return None

def finite(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError):
        return None

def build_catalog():
    values = [os.environ.get('APP_KEY','')]
    secrets=APP/'.streamlit/secrets.toml'
    if secrets.is_file():
        values.append(str(tomllib.loads(secrets.read_text()).get('APP_KEY','')))
    hashes=list({hashlib.sha256(x.encode()).hexdigest() for x in values})
    root=APP/'cache/VERIFICATION_RESULTS'
    rows={}; datasets={}; accepted=0; skipped=0; failed=0; newest=0
    reasons={}
    for namespace in ('lead-time-v1','seven-day-v1'):
        for path in sorted((root/namespace).glob('*.pkl')):
            try:
                # Only app-owned, fixed-location cache files; no user-supplied paths.
                record=pickle.loads(path.read_bytes())
                if record.get('schema')!=1:
                    skipped+=1; continue
                result=record['result']; meta=metadata(path,result,hashes,record.get("parameters"))
                if not meta:
                    skipped+=1; continue
                accepted+=1; created=float(record['created_at']); updated=float(record.get('updated_at',created)); newest=max(newest,updated)
                frame=result['per_cycle'] if meta['source']=='weekly' else result[0]
                dataset_id=f"{meta['source']}-{meta['kind']}-{meta['accumulation'] or meta['level'] or '2m'}"
                if meta.get("pilot"):dataset_id="wn3-pilot-"+dataset_id
                datasets.setdefault(dataset_id,dict(id=dataset_id,**meta))
                failures=result.get('failures') if meta['source']=='weekly' else result[1]
                if failures is not None and hasattr(failures,'to_dict'):
                    for problem in failures.to_dict('records'):
                        model=problem.get('模式');message=str(problem.get('原因',''))
                        if model not in MODELS:continue
                        reason=('旧GRIB归档未覆盖；可切换EC/GFS本地0.25°归档评分' if '本地模式归档' in message else '历史计算记录了跨磁盘落盘失败' if 'cross-device' in message else '该批次资料获取或配对失败')
                        reasons.setdefault(dataset_id,{}).setdefault(model,{})[reason]=reasons.get(dataset_id,{}).get(model,{}).get(reason,0)+1
                for raw in frame.to_dict('records'):
                    run=raw['起报时间（BJT）']; valid=raw['有效时间（BJT）']
                    row=dict(dataset=dataset_id, model=raw['模式'], run=run.isoformat(), valid=valid.isoformat(),
                        lead=int((valid-run).total_seconds()/3600), threshold=finite(raw.get('阈值(mm)')),
                        n=finite(raw.get('有效配对')), created=created, updated=updated)
                    if meta['kind']=='降水':
                        row.update(H=int(raw['命中H']),M=int(raw['漏报M']),F=int(raw['空报F']))
                    else:
                        mapping={'平均误差':'ME','平均绝对误差':'MAE','均方根误差':'RMSE','相关系数':'CC',
                            '高度偏差(m)':'ME','高度MAE(m)':'MAE','高度RMSE(m)':'RMSE','高度空间相关':'CC','风矢量RMSE(m/s)':'VRMSE'}
                        row.update({new:finite(raw[old]) for old,new in mapping.items() if old in raw})
                    identity=(dataset_id,row['model'],row['run'],row['lead'],row['threshold'])
                    if identity not in rows or created>rows[identity]['created']:
                        rows[identity]=row
            except Exception:
                failed+=1
    # Keep each cycle from one calculation batch. Mixing an older model row
    # into a newer cycle could silently change its paired spatial sample.
    cohorts={}
    for r in rows.values():
        key=(r['dataset'],r['run'],r['lead'])
        cohorts[key]=max(cohorts.get(key,0),r['created'])
    data=[r for r in rows.values() if r['created']==cohorts[(r['dataset'],r['run'],r['lead'])]]
    for item in datasets.values():
        items=[r for r in data if r['dataset']==item['id']]
        item['supported_models']=item['models']
        item['models']=[m for m in MODELS if any(r['model']==m for r in items)]
        item['model_issues']={m:max(counts,key=counts.get) for m,counts in reasons.get(item['id'],{}).items()}
        item['start']=min(r['valid'] for r in items); item['end']=max(r['valid'] for r in items)
        item['leads']=sorted({r['lead'] for r in items})
        item['thresholds']=sorted({r['threshold'] for r in items if r['threshold'] is not None})
        item['times']=sorted({r['valid'] for r in items})
        if item.get('pilot'):item['source_note']='WeatherNext 3小范围试接入：深圳2米气温官方0.1°集合均值，UTC00起报/+24h，按已有市域格点双线性插值匹配；当前单时次结果只用于接入验证。'
        item['reference']='GDAS 分析场' if item['kind']=='环流' else '深圳开放数据格点分析场'
        item['region']='108–120°E / 18–27°N' if item['kind']=='环流' else '深圳市行政区域'
        item['updated']=datetime.fromtimestamp(max(r.get('updated',r['created']) for r in items),BJT).isoformat()
    native_status='ok'
    try:
        from native_archive import cached_native
        native_sets,native_rows,_=cached_native(APP,Path('/home/chengyang/gods-eye-weather/weather-data'),Path(__file__).with_name('native-scores.json'))
        data.extend(native_rows)
        for d in native_sets:datasets[d['id']]=d
    except Exception:
        native_status='本地模式归档评分暂不可用，请刷新重试'
    analysis_status='ok'
    try:
        from analysis_verification import catalog as analysis_catalog
        analysis=analysis_catalog()
        data.extend(analysis['rows'])
        for d in analysis['datasets']:datasets[d['id']]=d
    except Exception:
        analysis_status='ERA5/地面分析场评分暂不可用，请刷新重试'
    openmeteo_status='ok'
    try:
        from openmeteo_verification import catalog as openmeteo_catalog, merge_into_catalog
        openmeteo=openmeteo_catalog()
        merge_into_catalog(openmeteo,datasets,data)
    except Exception:
        openmeteo_status='EC 9 km · Open-Meteo本地评分暂不可用'
    native_ids={d['id'] for d in datasets.values() if d.get('source')=='hk'}
    wn3_native=[r for r in data if r['model']==WN3 and r['dataset'] in native_ids]
    wn3_pairs={(r['run'],r['lead'],r['valid']) for r in wn3_native}
    wn3_status=dict(model=WN3,source='local-native-cache',grid_degrees=0.1,statistic='mean',
        status='available' if wn3_native else ('waiting-for-paired-data' if native_status=='ok' else 'unavailable'),
        paired_forecasts=len(wn3_pairs),first_valid_bjt=min((r['valid'] for r in wn3_native),default=None),
        last_valid_bjt=max((r['valid'] for r in wn3_native),default=None),
        elements=[dict(kind=kind,accumulation=3 if kind=='降水' else None,
            paired_forecasts=len({(r['run'],r['lead'],r['valid']) for r in wn3_native
                if ('降水' if 'H' in r else '气温')==kind})) for kind in ('气温','降水')],
        notice='WN3使用本机原生0.1°集合均值；标准2米气温和连续完整小时段组成的3小时降水。只统计已有预报与实况完成配对的时次，不下载上游资料。')
    return dict(version=2, generated=datetime.now(BJT).isoformat(), source_updated=datetime.fromtimestamp(newest,BJT).isoformat() if newest else None,
        datasets=list(datasets.values()), rows=data,native_status=native_status,analysis_status=analysis_status,openmeteo_status=openmeteo_status,weathernext3=wn3_status,
        status=dict(accepted=accepted,unverified=skipped,unreadable=failed),
        notice='仅展示已有真实检验结果；未保存的累计时长或层次不推测，未提供样本总数时不计算 ETS。')

if __name__=='__main__':
    import sys
    result=build_catalog()
    if len(sys.argv)>1:
        Path(sys.argv[1]).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf8')
    print(json.dumps({'status':result['status'],'rows':len(result['rows']),'datasets':result['datasets']},ensure_ascii=False))
