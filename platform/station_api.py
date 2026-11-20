"""Read-only station HTTP functions. No imports from fetch/score/archive code."""
from functools import lru_cache
from pathlib import Path
import guifan_scores as guifan
import json
import gzip
import re
import sqlite3
from urllib.parse import parse_qs
from datetime import date, datetime, timedelta
from station_common import (ROOT, XJ_BOUNDS, XINJIANG, MODELS, VARIABLES, DAY_WINDOWS, THRESHOLDS, GRID_DATASETS,
                            GRID_MODELS, GRID_THRESHOLDS, MOMENTS, UTC, cache_root, iso,
                            read_json, stamp, moment_score, event_score, bjt, CMAGD_BOX, CMAGD_MODELS, CMAGD_NOTICE, in_cmagd)

RANGES = {'0-240': (0,240), '0-24': (0,24), '25-72': (25,72), '73-168': (73,168), '169-240': (169,240)}
LEAD_GROUPS = {'near': (0,24), 'short': (25,72), 'medium': (73,168), 'extended': (169,240)}

# Same numeric/AI ordering as app.js numericFirst (including WN3 correction).
OVERVIEW_REGIONS = ('all', 'cmagd', 'hn', 'hun', 'gba', 'hk', 'sz', 'xj')
OVERVIEW_MODELS = ('ecmwf','ec01','ecens','gfs','gefs','wn3ml','aifs','aigfs','wn3')
# Same wording as the detailed pages' legend.
OVERVIEW_LABELS = {'wn3': 'WN3 集合平均'}


def overview(params):
    element, day, days = params.get('element','rain'), params.get('day','1'), params.get('days','7')
    if element not in ('rain','t') or day not in ('1','2','3') or days not in {str(d) for d in DAY_WINDOWS if d in (3,7,15)}:
        raise ValueError('请选择有效要素、天次和日期范围。')
    region = params.get('region', 'all')
    if region not in OVERVIEW_REGIONS:
        raise ValueError('检验区域不合法。')
    directory = snapshot()
    if directory is None:
        return dict(state='empty', rows=[], grades=[], station_count=0)
    # Absolute snapshot path includes both cache root and generation. Never mix
    # current.json and metadata read from two different generations.
    return overview_cached(str(directory), element, int(day), int(days), region)


@lru_cache(maxsize=192)
def overview_cached(path, element, day, days, region='all'):
    directory = Path(path)
    metadata = cached_json(str(directory/'meta.json'))
    end = effective_window_end(metadata, region); start = end-timedelta(days=days)
    adjusted = end < stamp(metadata['window_end'])
    variable = 'rain24' if element == 'rain' else 't'
    lo, hi = (day-1)*24, day*24
    # The producer's regional scores already exclude XINJIANG. Query persisted
    # pairs only for actual coverage and observations: grade A+C cannot recover
    # observation frequencies when a higher grade was forecast (table 3-1).
    south = [str(s['id']) for s in metadata.get('stations',[]) if str(s['id']) not in XINJIANG and not s.get('cmagd_extended')]
    models = CMAGD_MODELS if region == 'cmagd' else OVERVIEW_MODELS
    if region != 'all':
        # 用户 2026-11-11 要求总览可选区域；区域名单与详细分析页相同，并且取自同一代快照。
        south = [str(i) for i in region_stations(region, metadata.get('stations',[]))]
        if not south:
            return dict(state='empty', rows=[], grades=[], station_count=0, region=region)
    where = ('var=? AND valid>=? AND valid<=? AND '+('lead=?' if element=='rain' else 'lead>? AND lead<=?')+
             ' AND fc IS NOT NULL AND ob IS NOT NULL AND ABS(fc)<1e308 AND ABS(ob)<1e308'+
             ' AND station IN ('+','.join('?' for _ in south)+')'+
             # Naming every model lets SQLite seek the primary key instead of
             # reading each station's whole history (57 s -> 2 s on Spark).
             ' AND model IN ('+','.join('?' for _ in models)+')')
    args = [variable, iso(start), iso(end), *([hi] if element=='rain' else [lo,hi]), *south, *models]
    observed = [0]*6
    with sqlite3.connect((directory/'pairs.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        db.execute('PRAGMA cache_size=-4000')
        # 2026-11-15 用户要求修总览首开慢（新快照冷库要 6～13 分钟）：主键是 (model,run,lead,station,var)，
        # 只给 model 时每个模式整段扫。这里把可能的起报时间和时效逐个列出，主键前几列都能直接定位、只读需要的
        # 时效（冷库降水约 6～8 秒、气温约 40～60 秒，配对不变；有效时间=起报+时效已全库核对）。
        # 起报候选 = 窗口内每个整点 ∪ jobs 表登记的起报（防将来有非整点起报的模式）。
        leads = [hi] if element=='rain' else list(range(lo+1, hi+1))
        run_lo, run_hi = start-timedelta(hours=hi), end-timedelta(hours=leads[0])
        runs, hour = set(), run_lo.replace(minute=0, second=0, microsecond=0)
        if hour < run_lo: hour += timedelta(hours=1)
        while hour <= run_hi:
            runs.add(iso(hour)); hour += timedelta(hours=1)
        try:
            runs.update(run for (run,) in db.execute('SELECT DISTINCT run FROM jobs WHERE run>=? AND run<=?',
                                                     (iso(run_lo), iso(run_hi))))
        except sqlite3.OperationalError:
            pass
        runs = sorted(runs)
        where += (' AND run IN ('+','.join('?' for _ in runs)+') AND lead IN ('+','.join('?' for _ in leads)+')')
        args += [*runs, *leads]
        station_count, first, last, *forecast_extremes = db.execute(
            'SELECT COUNT(DISTINCT station),MIN(valid),MAX(valid),MAX(fc>=100 AND fc<250),MAX(fc>=250) FROM pairs WHERE '+where,args).fetchone()
        if element == 'rain':
            # One observed station/valid time per grade, shared across models.
            category = 'CASE '+ ' '.join(f'WHEN ob>={bound} THEN {k}' for k,bound in
                reversed(list(enumerate(guifan.BOUNDS[24],1))))+' ELSE 0 END'
            observed = [int(n or 0) for n in db.execute('SELECT '+','.join(f'SUM(grade={k})' for k in range(1,7))+
                ' FROM (SELECT DISTINCT station,valid,'+category+' AS grade FROM pairs WHERE '+where+')',args).fetchone()]
        score_path = directory/f'scores-{days}.json'
        rows = [r for r in cached_json(str(score_path)) if r['var']==variable and
                (r['lead']==hi if element=='rain' else lo<r['lead']<=hi)] if region=='all' and not adjusted and score_path.is_file() else []
        usable = bool(rows) and (all('N1' in r and 'N2' in r for r in rows) if element=='t' else
            all(any(r.get('grade')==k for r in rows if r['model']==m) for m in {r['model'] for r in rows} for k in range(1,7))
            and all(any(not r.get('grade') and r.get('threshold')==.1 and 'T' in r
                        for r in rows if r['model']==m) for m in {r['model'] for r in rows})
            # 2026-11-18 用户要求总览加累加检验：各量级阈值的计数也要齐，否则走下面的现算。
            and all(any(not r.get('grade') and r.get('threshold')==t for r in rows if r['model']==m)
                    for m in {r['model'] for r in rows} for t in guifan.BOUNDS[24]))
        if not usable:
            # Old/incomplete score products: aggregate existing pairs, without
            # writing files or invoking any forecast pairing/score producer.
            rows = []
            expressions = ['COUNT(*)','SUM(ABS(fc-ob))']+guifan.sql_expressions()
            if element=='rain':
                # 累加检验：预报、实况都达到该量级算报对（H），顺序 H、M、F，和 guifan.rain_rows 一致。
                for t in guifan.BOUNDS[24]:
                    expressions += [f'SUM(fc>={t} AND ob>={t})',f'SUM(fc<{t} AND ob>={t})',f'SUM(fc>={t} AND ob<{t})']
            for model, n, absolute, *counts in db.execute('SELECT model,'+','.join(expressions)+
                    ' FROM pairs WHERE '+where+' GROUP BY model', args):
                base = dict(model=model,n=n,var=variable,lead=hi)
                if element=='t':
                    rows.append(dict(base,MAE=absolute/n,**guifan.extras('t',counts)))
                else:
                    width = len(guifan.COLUMNS)
                    for i,t in enumerate(guifan.BOUNDS[24]):
                        h,m,f = (int(c or 0) for c in counts[width+3*i:width+3*i+3])
                        rows.append(dict(base,threshold=t,H=h,M=m,F=f,**(dict(T=counts[2]) if t==.1 else {})))
                    rows.extend(guifan.grade_rows(base,counts[:len(guifan.COLUMNS)]))
    result = []
    for model in models:
        selected = [r for r in rows if r['model']==model and r.get('n',0)>0]
        if not selected: continue
        if element=='t':
            n = sum(r['n'] for r in selected)
            n1,n2 = (sum(r[k] for r in selected) for k in ('N1','N2'))
            absolute = sum(r.get('absolute',r['MAE']*r['n']) for r in selected)
            values = dict(n=n,N1=n1,N2=n2,absolute=absolute,ACC1=n1/n,ACC2=n2/n,MAE=absolute/n)
        else:
            wet = [r for r in selected if not r.get('grade') and r.get('threshold')==.1]
            if not wet: continue
            n,h,m,f,t = (sum(r[k] for r in wet) for k in ('n','H','M','F','T'))
            grades = []
            for k in range(1,7):
                group = [r for r in selected if r.get('grade')==k]
                a,b,c = (sum(r[key] for r in group) for key in ('H','F','M'))
                grades.append(dict(grade=k,A=a,B=b,C=c,TS=a/(a+b+c) if a+b+c else None))
            cumulative = []
            for k,bound in enumerate(guifan.BOUNDS[24],1):
                group = [r for r in selected if not r.get('grade') and r.get('threshold')==bound]
                a,b,c = (sum(r[key] for r in group) for key in ('H','F','M'))
                cumulative.append(dict(grade=k,threshold=bound,A=a,B=b,C=c,TS=a/(a+b+c) if a+b+c else None))
            values = dict(n=n,H=h,M=m,F=f,T=t,PC=(n-m-f+t)/n,grades=grades,cumulative=cumulative)
        result.append(dict(model=model,label=OVERVIEW_LABELS.get(model,MODELS[model]),**values))
    grades = [dict(g,observed=observed[g['id']-1],threshold=guifan.BOUNDS[24][g['id']-1],
                   observed_cumulative=sum(observed[g['id']-1:])) for g in guifan.metadata(hours=24)['grades']
              if g['id']<=4 or observed[g['id']-1] or
              forecast_extremes[g['id']-5]] if element=='rain' else []
    return dict(state='ready' if result else 'empty',generation=directory.name,element=element,day=day,days=days,region=region,
                window_start=iso(start),window_end=iso(end),start=bjt(iso(start)),end=bjt(iso(end)),
                coverage_start=first,coverage_end=last,station_count=station_count,grades=grades,rows=result,
                **(dict(data_end_reason='nmic') if adjusted else {}))


def snapshot():
    root = cache_root(); current = read_json(root / 'current.json', {})
    generation = current.get('generation', '')
    if not re.fullmatch(r'\d{8}T\d{6}(?:\d{6})?', generation):
        return None
    return root / 'snapshots' / generation


@lru_cache(maxsize=12)
def cached_json(path):
    return json.loads(Path(path).read_text())


def meta():
    # Geography is versioned with the UI, independently of immutable score
    # snapshots. Older snapshots must not retain a truncated satellite outline.
    basemap = cached_json(str(ROOT / 'station-basemap.json'))
    directory = snapshot()
    if directory is None:
        table = read_json(ROOT / 'stations.json', {'stations':[]})
        return dict(state='empty',stations=table['stations'],sources={},models=[],variables=[],
                    notice='站点资料尚未刷新。请由维护脚本准备资料。', **basemap)
    # Snapshots published before the 1/3-day windows existed only carry 7/30.
    windows = [d for d in DAY_WINDOWS if (directory/f'scores-{d}.json').is_file()]
    metadata = cached_json(str(directory/'meta.json'))
    return dict(metadata, **window_metadata(metadata), state='ready', windows=windows, **basemap,
                cmagd_models=CMAGD_MODELS, cmagd_notice=CMAGD_NOTICE)


def parameters(query, allowed):
    parsed = parse_qs(query, max_num_fields=12, keep_blank_values=True, strict_parsing=True) if query else {}
    if set(parsed) - set(allowed) or any(len(v) != 1 or not v[0] for v in parsed.values()):
        raise ValueError('站点检验参数不合法或重复。')
    return {k:v[0] for k,v in parsed.items()}


def selection(params):
    variable = params.get('var','t'); days = params.get('days','15')
    if variable not in VARIABLES or days not in {str(d) for d in DAY_WINDOWS}:
        raise ValueError('请选择有效要素和日期范围。')
    return variable,int(days)


def scores(params):
    variable, days = selection(params)
    models = params.get('models','ec01,ecmwf').split(',')
    lead_range = params.get('range','0-240')
    if len(models) != len(set(models)) or not models or any(m not in MODELS for m in models) or lead_range not in RANGES:
        raise ValueError('模式或预报时效范围不合法。')
    directory = snapshot()
    if directory is None:
        return dict(rows=[],station_rows=[],state='empty')
    lo,hi = RANGES[lead_range]
    if not (directory/f'scores-{days}.json').is_file():
        raise ValueError(f'近{days}天站点评分将在下次定时评分后提供。')
    rows = [r for r in cached_json(str(directory/f'scores-{days}.json')) if r['var']==variable and r['model'] in models and lo<=r['lead']<=hi]
    stations = [r for r in cached_json(str(directory/f'station-scores-{days}.json')) if r['var']==variable and r['model'] in models
                and r['lead_min']==lo and r['lead_max']==hi]
    return dict(rows=rows,station_rows=stations,days=days,var=variable,models=models,range=lead_range,
                generated=cached_json(str(directory/'meta.json'))['generated'],
                sample='各模式各自有效配对；n为站点×起报×时效配对数。')


def station(params):
    variable,days = selection(params)
    hour = run_hour(params)
    model = params.get('model','ec01'); ident = params.get('id','')
    group = params.get('group'); leads_param = params.get('leads')
    if model not in MODELS:
        raise ValueError('模式或时效不合法。')
    if group is None and leads_param is None:
        text = params.get('lead','24')
        if not re.fullmatch(r'\d{1,3}',text) or int(text)>240:
            raise ValueError('模式或时效不合法。')
    elif group is not None and group not in LEAD_GROUPS:
        raise ValueError('时效分组不合法。')
    elif leads_param is not None:
        if not re.fullmatch(r'\d{1,3}(?:,\d{1,3}){0,240}',leads_param) or any(int(x)>240 for x in leads_param.split(',')):
            raise ValueError('时效列表不合法。')
    directory = snapshot()
    metadata = meta(); selected = next((s for s in metadata['stations'] if s['id']==ident),None)
    if selected is None:
        raise ValueError('未知站点。')
    if directory is None:
        return dict(station=selected,series=[],stats=[],state='empty')
    # 2026-11-15 近24小时可能按国家站资料截止（meta.window_ends），单站明细要和评分用同一段时间。
    stop = (metadata.get('window_ends') or {}).get(str(days), metadata['window_end'])
    cutoff = iso(stamp(stop)-timedelta(days=days))
    custom = 'start' in params or 'end' in params
    if custom:
        start, end = date_range(params)
        cutoff = iso(datetime.fromisoformat(start).replace(tzinfo=UTC)-timedelta(hours=8))
        stop = iso(datetime.fromisoformat(end).replace(tzinfo=UTC)+timedelta(days=1,hours=-8))
    uri = (directory/'pairs.sqlite').as_uri()+'?mode=ro&immutable=1'
    if group is not None or leads_param is not None:
        if group is not None:
            lo, hi = LEAD_GROUPS[group]
            m_info = next((m for m in metadata.get('models',[]) if m['id']==model),None)
            leads = [h for h in m_info['leads'] if lo <= h <= hi] if m_info and m_info.get('leads') else list(range(lo,hi+1))
        else:
            leads = sorted(set(map(int,leads_param.split(','))))
        if not leads:
            return dict(station=selected,stats=[],series=[],pair_count=0,group=group,leads=[],model=model,
                        var=variable,days=days,generated=metadata['generated'],limit=1000,hour=hour)
        with sqlite3.connect(uri,uri=True) as db:
            has_idx = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='pair_series'").fetchone())
            idx_sql = 'INDEXED BY pair_series' if has_idx else ''
            ph = ','.join('?' for _ in leads)
            stop_op = 'valid<?' if custom else 'valid<=?'
            hour_clause = '' if hour == 'all' else ' AND substr(run,12,2)=?'
            where = (f"WHERE station=? AND model=? AND var=? AND lead IN ({ph}) AND valid>=? AND {stop_op}"
                     f"{hour_clause} AND fc IS NOT NULL AND ob IS NOT NULL AND ABS(fc)<1e308 AND ABS(ob)<1e308")
            args = (ident,model,variable,*leads,cutoff,stop)
            if hour != 'all': args += (f'{(int(hour)-8)%24:02d}',)
            data = db.execute(f'''SELECT valid,AVG(fc),AVG(ob),COUNT(*),MAX(source),MAX(trace),MAX(report_time)
                    FROM pairs {idx_sql} {where} GROUP BY valid ORDER BY valid DESC LIMIT 1000''',args).fetchall()
            stats = []
            if variable in THRESHOLDS:
                exprs = ['COUNT(*)'] + [f'SUM(CASE WHEN {c} THEN 1 ELSE 0 END)'
                    for thr in THRESHOLDS[variable]
                    for c in (f'fc>={thr} AND ob>={thr}',f'fc<{thr} AND ob>={thr}',f'fc>={thr} AND ob<{thr}')]
                cnt, *vals = db.execute(f'SELECT {",".join(exprs)} FROM pairs {idx_sql} {where}',args).fetchone()
                pair_count = int(cnt or 0)
                for i, thr in enumerate(THRESHOLDS[variable]):
                    h, m_cnt, f = (int(vals[3*i+k] or 0) for k in range(3))
                    stats.append(dict(station=ident,model=model,var=variable,threshold=thr,**event_score(pair_count,h,m_cnt,f)))
            else:
                vals = db.execute(f'''SELECT COUNT(*),SUM(fc-ob),SUM(ABS(fc-ob)),SUM((fc-ob)*(fc-ob)),
                    SUM(fc),SUM(ob),SUM(fc*fc),SUM(ob*ob),SUM(fc*ob) FROM pairs {idx_sql} {where}''',args).fetchone()
                pair_count = int(vals[0] or 0)
                stats.append(dict(station=ident,model=model,var=variable,**moment_score([pair_count]+[float(x or 0.) for x in vals[1:]])))
            stats = enrich_stats(db,where,args,variable,stats)
        series = [dict(valid=r[0],fc=r[1],ob=r[2],n=int(r[3]),source=r[4],trace=r[5],report_time=r[6]) for r in reversed(data)]
        return dict(station=selected,stats=stats,series=series,pair_count=pair_count,group=group,leads=leads,
                    model=model,var=variable,days=days,generated=metadata['generated'],limit=1000,hour=hour)
    with sqlite3.connect(uri,uri=True) as db:
        # Every score was prepared offline; requests only select persisted rows.
        stats = ([] if custom or hour != 'all' else [json.loads(r[0]) for r in db.execute('SELECT stats FROM detail_stats WHERE station=? AND model=? AND var=? AND lead=? AND days=?',
                                                    (ident,model,variable,int(text),days))])
        hour_clause = '' if hour == 'all' else ' AND substr(run,12,2)=?'
        args = (ident,model,variable,int(text),cutoff,stop)
        if hour != 'all': args += (f'{(int(hour)-8)%24:02d}',)
        data = db.execute('''SELECT valid,run,fc,ob,source,trace,report_time FROM pairs
                WHERE station=? AND model=? AND var=? AND lead=? AND valid>=? AND ''' + ('valid<?' if custom else 'valid<=?') + '''
                ''' + hour_clause + ' ORDER BY valid DESC LIMIT 1000',args).fetchall()
        extra_where='WHERE station=? AND model=? AND var=? AND lead=? AND valid>=? AND '+('valid<?' if custom else 'valid<=?')+hour_clause
        stats=enrich_stats(db,extra_where,args,variable,stats)
    series = [dict(zip(['valid','run','fc','ob','source','trace','report_time'],r)) for r in reversed(data)]
    return dict(station=selected,stats=stats,series=series,model=model,var=variable,lead=int(text),days=days,
                generated=metadata['generated'],limit=1000,hour=hour)


def enrich_stats(db,where,args,variable,stats):
    values=db.execute('SELECT '+','.join(guifan.sql_expressions())+' FROM pairs '+where,args).fetchone()
    if not stats:
        # Custom date/hour selections previously had only series. Compute from all
        # selected pairs, never from the 1000-point display limit or averaged series.
        if variable in THRESHOLDS:
            for t in THRESHOLDS[variable]:
                n,h,m,f=db.execute(f'SELECT COUNT(*),SUM(fc>={t} AND ob>={t}),SUM(fc<{t} AND ob>={t}),SUM(fc>={t} AND ob<{t}) FROM pairs '+where,args).fetchone()
                stats.append(dict(threshold=t,**event_score(n,h or 0,m or 0,f or 0)))
        else:
            moments=db.execute('SELECT COUNT(*),SUM(fc-ob),SUM(ABS(fc-ob)),SUM((fc-ob)*(fc-ob)),SUM(fc),SUM(ob),SUM(fc*fc),SUM(ob*ob),SUM(fc*ob) FROM pairs '+where,args).fetchone()
            stats=[moment_score([v or 0 for v in moments])]
    for row in stats:
        row.update(guifan.extras(variable,values,row.get('threshold')));guifan.derived(row)
    if variable=='rain24' and not any(r.get('grade') for r in stats):
        stats.extend(guifan.derived(r) for r in guifan.grade_rows(dict(n=stats[0]['n']),values))
    return stats


def run_hour(params):
    hour = params.get('hour', 'all')
    if hour not in {'all', '02', '08', '14', '20'}:
        raise ValueError('请选择合法的北京时间起报时次。')
    return hour


def date_range(params):
    try:
        start, end = params['start'], params['end']
        if not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', s) for s in (start, end)):
            raise ValueError()
        if date.fromisoformat(start) > date.fromisoformat(end): raise ValueError()
    except (KeyError, ValueError):
        raise ValueError('请选择合法的北京时间起止日期（YYYY-MM-DD）。') from None
    return start, end


def grid_snapshot(variable=None):
    """Keep new surface products readable while an older in-flight worker finishes."""
    directory = snapshot()
    if directory is None or variable not in ('msl', 'wind') or (directory/f'grid-rows-{variable}.json.gz').is_file():
        return directory
    candidates = sorted((p for p in directory.parent.iterdir()
                         if re.fullmatch(r'\d{8}T\d{6}(?:\d{6})?', p.name)
                         and (p/f'grid-rows-{variable}.json.gz').is_file()), reverse=True)
    return candidates[0] if candidates else directory


def grid_datasets():
    def datasets_at(directory):
        path = directory/'meta.json'
        metadata = cached_json(str(path)) if path.is_file() else {}
        return [dict(dataset, grid_snapshot=directory.name, window_end=metadata.get('window_end'),
                     **window_metadata(metadata))
                for dataset in read_json(directory/'grid-datasets.json', [])]
    directory = snapshot()
    datasets = datasets_at(directory) if directory else []
    for variable in ('msl', 'wind'):
        if any(d.get('var') == variable for d in datasets): continue
        fallback = grid_snapshot(variable)
        if fallback and fallback != directory:
            datasets.extend(d for d in datasets_at(fallback) if d.get('var') == variable)
    return datasets


def grid_rows_file(params):
    variable = next((v for v, ident in GRID_DATASETS.items() if ident == params.get('dataset')), None)
    if variable is None: raise ValueError('请选择有效站点资料集。')
    directory = grid_snapshot(variable)
    generation = params.get('generation')
    if generation is not None:
        if not re.fullmatch(r'\d{8}T\d{6}(?:\d{6})?', generation):
            raise ValueError('站点资料版本不合法。')
        if directory is not None:
            directory = directory.parent/generation
    if directory is None or not (directory/f'grid-rows-{variable}.json.gz').is_file():
        raise ValueError('站点共用图表将在下次离线评分后提供。')
    return directory/f'grid-rows-{variable}.json.gz'


# 华南 = 广东、广西、海南、香港、澳门。按站名逐个核对（2026-11-09，206 站中 57 站）；
# 新站按省码自动归入，保留旧站名单兼容历史快照。香港/澳门和深圳区站按前缀。
HUANAN_IDS = frozenset('''57957 59023 59046 59058 59072 59082 59087 59096 59117 59211 59254 59265
59278 59287 59293 59316 59417 59431 59456 59493 59501 59632 59644 59658 59663 59673 59758 59792
59838 59845 59855 59948 ZGGG ZGKL ZGNN ZGOW ZGSZ ZJHK ZJSY VHHH VMMC'''.split())
REGION_BOUNDS = {'cmagd': CMAGD_BOX, 'xj': XJ_BOUNDS, 'gba': (111.9, 21.6, 115.3, 23.5), 'hk': (113.8, 22.14, 114.5, 22.56), 'sz': (113.7, 22.44, 114.65, 22.88)}


def region_stations(region, stations=None):
    if stations is None: stations = meta().get('stations', [])
    if region == 'cmagd':
        return [s['id'] for s in stations if in_cmagd(s['lon'], s['lat'])]
    stations = [s for s in stations if not s.get('cmagd_extended')]
    if region == 'hn':
        return [s['id'] for s in stations if str(s.get('province_code')) in ('44', '45', '46') or
                str(s['id']) in HUANAN_IDS or str(s['id']).startswith(('450', 'SZ-'))]
    if region == 'hun':
        # 湖南地区（用户 2026-11-12 定，和华南地区并列）：按国家站省码 43 归入。
        return [s['id'] for s in stations if str(s.get('province_code')) == '43']
    if region == 'hk':
        return [s['id'] for s in stations if (str(s['id']).startswith('450') and s['id'] != '45011') or s['id'] == 'VHHH']  # 45011 is Macau
    if region == 'sz':
        w, s_lat, e, n_lat = REGION_BOUNDS['sz']
        return [s['id'] for s in stations if str(s['id']).startswith('SZ-') or s['id'] in ('59493', 'ZGSZ') or
                ('nmic' in s.get('sources', []) and w <= s['lon'] <= e and s_lat <= s['lat'] <= n_lat)]
    if region in REGION_BOUNDS:
        w, s_lat, e, n_lat = REGION_BOUNDS[region]
        return [s['id'] for s in stations if w <= s['lon'] <= e and s_lat <= s['lat'] <= n_lat]
    return [s['id'] for s in stations]


def nmic_majority(region, stations):
    """Count station membership, including mixed sources; exactly half is false."""
    # 南方地区是全部站点的默认视图，用户 2026-11-11 定它照旧用最新资料，
    # 不参与这条规则（按真实站点表它恰好 176/351，会被「过半」误判）。
    if region == 'all' or not any('nmic' in s.get('sources', []) for s in stations):
        return False
    ids = set(region_stations(region, stations))
    members = [s for s in stations if s['id'] in ids]
    return sum('nmic' in s.get('sources', []) for s in members) * 2 > len(members)


def effective_window_end(metadata, region):
    end = stamp(metadata['window_end'])
    latest = (metadata.get('cmagd_nmic_end') or metadata.get('nmic_end')) if region == 'cmagd' else metadata.get('nmic_end')
    if latest and nmic_majority(region, metadata.get('stations', [])):
        end = min(end, stamp(latest))
    return end


def window_metadata(metadata):
    return dict(nmic_end=metadata.get('nmic_end'),
                cmagd_nmic_end=metadata.get('cmagd_nmic_end') or metadata.get('nmic_end'),
                region_nmic_majority={region: nmic_majority(region, metadata.get('stations', []))
                                      for region in OVERVIEW_REGIONS})


def grid_rows(params):
    path = grid_rows_file(params)
    if 'start' not in params and 'end' not in params:
        if 'leads' in params or 'station' in params:
            raise ValueError('请同时选择日期范围。')
        return path
    start, end = date_range(params)
    leads = None
    if 'leads' in params:
        text = params['leads']
        if not re.fullmatch(r'\d{1,3}(?:,\d{1,3}){0,240}', text):
            raise ValueError('预报时效列表不合法。')
        leads = set(map(int, text.split(',')))
        if any(lead > 240 for lead in leads):
            raise ValueError('预报时效超出范围。')
    region = params.get('region', 'all')
    if region not in OVERVIEW_REGIONS:
        raise ValueError('检验区域不合法。')
    if 'station' in params:
        ident = params['station']
        metadata = cached_json(str(path.parent/'meta.json'))
        if not any(s['id'] == ident for s in metadata.get('stations', [])):
            raise ValueError('未知站点。')
        variable = next(v for v, name in GRID_DATASETS.items() if name == params['dataset'])
        cutoff = iso(datetime.fromisoformat(start).replace(tzinfo=UTC)-timedelta(hours=8))
        stop = iso(datetime.fromisoformat(end).replace(tzinfo=UTC)+timedelta(hours=16))
        where = ('station=? AND var=? AND valid>=? AND valid<? '
                 'AND fc IS NOT NULL AND ob IS NOT NULL AND ABS(fc)<1e308 AND ABS(ob)<1e308')
        args = [ident, variable, cutoff, stop]
        if leads is not None:
            ordered = sorted(leads)
            where += ' AND lead IN (' + ','.join('?' for _ in ordered) + ')'
            args.extend(ordered)
        database = path.parent/'pairs.sqlite'
        out = []
        with sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1', uri=True) as db:
            db.execute('PRAGMA cache_size=-4000')
            trace_column='trace' if any(r[1]=='trace' for r in db.execute('PRAGMA table_info(pairs)')) else 'NULL'
            for model, run, lead, valid, fc, ob, trace in db.execute(
                    f'SELECT model,run,lead,valid,fc,ob,{trace_column} FROM pairs WHERE {where} ORDER BY model,lead,valid', args):
                if model not in GRID_MODELS: continue
                base = dict(dataset=GRID_DATASETS[variable], model=GRID_MODELS[model], station=ident,
                            run=bjt(run), lead=int(lead), valid=bjt(valid), created=0, updated=0, n=1)
                if variable in THRESHOLDS:
                    for threshold in THRESHOLDS[variable]:
                        yf, yo = fc >= threshold, ob >= threshold
                        out.append(dict(base, threshold=threshold, H=int(yf and yo),
                                        M=int(not yf and yo), F=int(yf and not yo),**(dict(T=int(fc>=.1 and ob<.1 and bool(trace))) if threshold==.1 and trace is not None else {})))
                    if variable=='rain24':out.extend(guifan.rain_rows(base,[fc],[ob],24,[trace],thresholds=[]))
                else:
                    error = fc-ob
                    out.append(dict(base, threshold=None, fc=fc, ob=ob,
                                    ME=error, MAE=abs(error), RMSE=abs(error), CC=None,**(guifan.accuracy([fc],[ob]) if variable=='t' else {})))
        return out
    if region != 'all':
        variable = next((v for v, ident in GRID_DATASETS.items() if ident == params.get('dataset')), None)
        database = path.parent / 'grid-map.sqlite'
        st_ids = region_stations(region)
        if not st_ids or not database.is_file():
            return []
        cols = list(MOMENTS) if variable not in THRESHOLDS else ['n'] + [f'{k}{GRID_THRESHOLDS.index(th)}' for th in THRESHOLDS[variable] for k in ('h', 'm', 'f')]
        legacy_count=len(cols)
        with sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1',uri=True) as check:
            has_extra='N1' in {r[1] for r in check.execute('PRAGMA table_info(daily)')}
        if has_extra:cols+=list(guifan.COLUMNS)
        m_keys = list(CMAGD_MODELS if region == 'cmagd' else GRID_MODELS)
        ph_m = ','.join('?' for _ in m_keys)
        ph_s = ','.join('?' for _ in st_ids)
        where = f'WHERE model IN ({ph_m}) AND var=? AND day>=? AND day<=? AND station IN ({ph_s})'
        args = [*m_keys, variable, start, end, *st_ids]
        if leads is not None:
            s_leads = sorted(leads)
            where += ' AND lead IN (' + ','.join('?' for _ in s_leads) + ')'
            args.extend(s_leads)
        query = f'SELECT model,day,lead,{",".join(f"SUM({c})" for c in cols)} FROM daily {where} GROUP BY model,day,lead'
        out = []
        with sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
            db.execute('PRAGMA cache_size=-4000')
            for model, day, lead, *values in db.execute(query, args):
                valid = f'{day}T08:00:00'
                run = (datetime.fromisoformat(valid) - timedelta(hours=int(lead))).strftime('%Y-%m-%dT%H:%M:%S')
                base = dict(dataset=GRID_DATASETS[variable], model=GRID_MODELS[model], run=run, lead=int(lead), valid=valid, created=0, updated=0)
                if variable not in THRESHOLDS:
                    out.append(dict(base, threshold=None, **moment_score(values[:9]),**(guifan.extras(variable,values[legacy_count:]) if has_extra else {})))
                else:
                    n_val = int(values[0] or 0)
                    for i, th in enumerate(THRESHOLDS[variable]):
                        h, m_cnt, f = map(int, values[1 + 3*i : 1 + 3*i + 3])
                        out.append(dict(base, threshold=th, n=n_val, H=h, M=m_cnt, F=f,**(guifan.extras(variable,values[legacy_count:],th) if has_extra else {})))
                    if has_extra and variable=='rain24':out.extend(guifan.grade_rows(dict(base,n=n_val),values[legacy_count:]))
        return out
    # Select existing offline scores only; retain values and archive row order.
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        rows = json.load(handle)
    return [row for row in rows if start <= row['valid'][:10] <= end
            and (leads is None or row['lead'] in leads)]


def map_scores(params):
    start, end = date_range(params)
    hour = run_hour(params)
    variable, model = params.get('var', 't'), params.get('model', 'ec01')
    model = next((key for key, value in GRID_MODELS.items() if value == model), model)
    if variable not in GRID_DATASETS or model not in MODELS:
        raise ValueError('模式或要素不合法。')
    if 'lead' in params and 'leads' in params: raise ValueError('请选择单个时效或时效列表。')
    text = params.get('leads', params.get('lead', '24'))
    if not re.fullmatch(r'\d{1,3}(?:,\d{1,3}){0,240}', text): raise ValueError('时效列表不合法。')
    leads = sorted(set(map(int, text.split(','))))
    if any(lead > 240 for lead in leads): raise ValueError('时效超出范围。')
    threshold = None
    if variable in THRESHOLDS:
        try: threshold = float(params.get('threshold', '.1'))
        except ValueError: raise ValueError('降水阈值不合法。') from None
        if threshold not in THRESHOLDS[variable]: raise ValueError('降水阈值不合法。')
    directory = grid_snapshot(variable)
    if directory is None or not (directory/'grid-map.sqlite').is_file():
        return dict(rows=[], state='empty')
    columns = list(MOMENTS) if variable not in THRESHOLDS else ['n'] + [f'{k}{GRID_THRESHOLDS.index(threshold)}' for k in ('h','m','f')]
    legacy_count=len(columns)
    with sqlite3.connect((directory/'grid-map.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True) as check:
        has_extra='N1' in {r[1] for r in check.execute('PRAGMA table_info(daily)')}
    if has_extra:columns+=list(guifan.COLUMNS)
    query = 'SELECT station,' + ','.join(f'SUM({c})' for c in columns) + ''' FROM daily
             WHERE model=? AND var=? AND day>=? AND day<=? AND lead IN (''' + ','.join('?' for _ in leads) + ')'
    args = (model, variable, start, end, *leads)
    if 'station' in params:
        query += ' AND station=?'; args += (params['station'],)
    query += ' GROUP BY station'
    database = directory/'grid-map.sqlite'
    if hour != 'all':
        # Daily aggregates combine cycles. Filter persisted pairs before aggregating.
        cutoff = datetime.fromisoformat(start).replace(tzinfo=UTC)-timedelta(hours=8)
        stop = datetime.fromisoformat(end).replace(tzinfo=UTC)+timedelta(hours=16)
        expressions = ('COUNT(*),SUM(fc-ob),SUM(ABS(fc-ob)),SUM((fc-ob)*(fc-ob)),'
                       'SUM(fc),SUM(ob),SUM(fc*fc),SUM(ob*ob),SUM(fc*ob)')
        if variable in THRESHOLDS:
            expressions = ('COUNT(*),SUM(CASE WHEN fc>=:threshold AND ob>=:threshold THEN 1 ELSE 0 END),'
                           'SUM(CASE WHEN fc<:threshold AND ob>=:threshold THEN 1 ELSE 0 END),'
                           'SUM(CASE WHEN fc>=:threshold AND ob<:threshold THEN 1 ELSE 0 END)')
        legacy_count=4 if variable in THRESHOLDS else 9
        expressions+=','+','.join(guifan.sql_expressions());has_extra=True
        placeholders = ','.join(f':lead{i}' for i in range(len(leads)))
        query = f'''SELECT station,{expressions} FROM pairs
                    WHERE model=:model AND var=:var AND run>=:run_start AND run<:run_stop
                    AND valid>=:start AND valid<:stop AND lead IN ({placeholders})
                    AND substr(run,12,2)=:hour
                    AND fc IS NOT NULL AND ob IS NOT NULL AND ABS(fc)<1e308 AND ABS(ob)<1e308'''
        args = dict(model=model, var=variable, start=iso(cutoff), stop=iso(stop),
                    run_start=iso(cutoff-timedelta(hours=max(leads))),
                    run_stop=iso(stop-timedelta(hours=min(leads))),
                    hour=f'{(int(hour)-8)%24:02d}', threshold=threshold,
                    **{f'lead{i}':lead for i,lead in enumerate(leads)})
        if 'station' in params:
            query += ' AND station=:station'; args['station'] = params['station']
        query += ' GROUP BY station'
        database = directory/'pairs.sqlite'
    rows = []
    with sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1', uri=True) as db:
        db.execute('PRAGMA cache_size=-2000')
        for ident, *values in db.execute(query, args):
            result = moment_score(values[:legacy_count]) if variable not in THRESHOLDS else event_score(*map(int, values[:legacy_count]))
            if has_extra:result.update(guifan.extras(variable,values[legacy_count:],threshold))
            grade=params.get('grade')
            if grade is not None:
                if variable!='rain24' or grade not in {str(i) for i in range(1,7)}:raise ValueError('降水量级不合法。')
                if not has_extra:continue
                result=guifan.grade_rows(dict(n=int(values[0])),values[legacy_count:])[int(grade)-1]
            guifan.derived(result)
            result['n'] = int(result['n'])
            rows.append(dict(dict(station=ident, model=model, var=variable, threshold=threshold), **result))
    return dict(rows=rows, model=model, var=variable, start=start, end=end, leads=leads, threshold=threshold,hour=hour,
                sample='地图合并所选日期、起报时次与时效的站点配对；CC为单站时间相关。主图连续量按时效/时刻等权，CC为平均站间空间相关；共同有效时次及单个时刻筛选仅用于主图。')


def station_leads(params):
    """Forecast lead times that actually have pairs for this station/model/variable."""
    variable,_ = selection(params)
    model = params.get('model','ec01'); ident = params.get('id','')
    if model not in MODELS:
        raise ValueError('模式不合法。')
    directory = snapshot()
    if directory is None:
        return dict(leads=[])
    uri = (directory/'pairs.sqlite').as_uri()+'?mode=ro&immutable=1'
    with sqlite3.connect(uri,uri=True) as db:
        leads = [r[0] for r in db.execute('SELECT DISTINCT lead FROM detail_stats WHERE station=? AND model=? AND var=? ORDER BY lead',
                                          (ident,model,variable))]
    return dict(station=ident,model=model,var=variable,leads=leads)


def dispatch(operation,query):
    allowed = {'meta':set(),'overview':{'element','day','days','region'},'scores':{'var','models','days','range'},
               'station':{'id','var','model','lead','leads','group','days','start','end','hour'},
               'series':{'station','var','model','lead','leads','group','start','end','hour'},
               'station-leads':{'id','var','model'},
               'grid-datasets':set(), 'grid-rows':{'dataset','start','end','generation','leads','region','station'},
               'map':{'grade','model','var','threshold','start','end','lead','leads','station','hour'}}
    if operation not in allowed:
        raise KeyError(operation)
    params = parameters(query,allowed[operation])
    if operation == 'meta': return meta()
    if operation == 'overview': return overview(params)
    if operation == 'scores': return scores(params)
    if operation == 'grid-datasets': return grid_datasets()
    if operation == 'grid-rows': return grid_rows(params)
    if operation == 'map': return map_scores(params)
    if operation == 'station-leads': return station_leads(params)
    if operation == 'series':
        params['id'] = params.pop('station', '')
        params['model'] = next((key for key, value in GRID_MODELS.items() if value == params.get('model')), params.get('model', 'ec01'))
        date_range(params)
    return station(params)
