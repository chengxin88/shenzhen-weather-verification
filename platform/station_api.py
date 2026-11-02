"""Read-only station HTTP functions. No imports from fetch/score/archive code."""
from functools import lru_cache
from pathlib import Path
import json
import gzip
import re
import sqlite3
from urllib.parse import parse_qs
from datetime import date, datetime, timedelta
from station_common import (ROOT, MODELS, VARIABLES, DAY_WINDOWS, THRESHOLDS, GRID_DATASETS,
                            GRID_MODELS, GRID_THRESHOLDS, MOMENTS, UTC, cache_root, iso,
                            read_json, stamp, moment_score, event_score)

RANGES = {'0-168': (0,168), '0-24': (0,24), '25-72': (25,72), '73-168': (73,168)}


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
    return dict(cached_json(str(directory/'meta.json')), state='ready', windows=windows, **basemap)


def parameters(query, allowed):
    parsed = parse_qs(query, max_num_fields=12, keep_blank_values=True, strict_parsing=True) if query else {}
    if set(parsed) - set(allowed) or any(len(v) != 1 or not v[0] for v in parsed.values()):
        raise ValueError('站点检验参数不合法或重复。')
    return {k:v[0] for k,v in parsed.items()}


def selection(params):
    variable = params.get('var','t'); days = params.get('days','30')
    if variable not in VARIABLES or days not in {str(d) for d in DAY_WINDOWS}:
        raise ValueError('请选择有效要素和日期范围。')
    return variable,int(days)


def scores(params):
    variable, days = selection(params)
    models = params.get('models','ec01,ecmwf').split(',')
    lead_range = params.get('range','0-168')
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
    model = params.get('model','ec01'); ident = params.get('id',''); text = params.get('lead','24')
    if model not in MODELS or not re.fullmatch(r'\d{1,3}',text) or int(text)>168:
        raise ValueError('模式或时效不合法。')
    directory = snapshot()
    metadata = meta(); selected = next((s for s in metadata['stations'] if s['id']==ident),None)
    if selected is None:
        raise ValueError('未知站点。')
    if directory is None:
        return dict(station=selected,series=[],stats=[],state='empty')
    cutoff = iso(stamp(metadata['window_end'])-timedelta(days=days))
    stop = metadata['window_end']
    custom = 'start' in params or 'end' in params
    if custom:
        start, end = date_range(params)
        cutoff = iso(datetime.fromisoformat(start).replace(tzinfo=UTC)-timedelta(hours=8))
        stop = iso(datetime.fromisoformat(end).replace(tzinfo=UTC)+timedelta(days=1,hours=-8))
    uri = (directory/'pairs.sqlite').as_uri()+'?mode=ro&immutable=1'
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
    series = [dict(zip(['valid','run','fc','ob','source','trace','report_time'],r)) for r in reversed(data)]
    return dict(station=selected,stats=stats,series=series,model=model,var=variable,lead=int(text),days=days,
                generated=metadata['generated'],limit=1000,hour=hour)


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


def grid_datasets():
    directory = snapshot()
    return [dict(dataset, grid_snapshot=directory.name)
            for dataset in read_json(directory/'grid-datasets.json', [])] if directory else []


def grid_rows_file(params):
    variable = next((v for v, ident in GRID_DATASETS.items() if ident == params.get('dataset')), None)
    if variable is None: raise ValueError('请选择有效站点资料集。')
    directory = snapshot()
    generation = params.get('generation')
    if generation is not None:
        if not re.fullmatch(r'\d{8}T\d{6}(?:\d{6})?', generation):
            raise ValueError('站点资料版本不合法。')
        if directory is not None:
            directory = directory.parent/generation
    if directory is None or not (directory/f'grid-rows-{variable}.json.gz').is_file():
        raise ValueError('站点共用图表将在下次离线评分后提供。')
    return directory/f'grid-rows-{variable}.json.gz'


def grid_rows(params):
    path = grid_rows_file(params)
    if 'start' not in params and 'end' not in params:
        if 'leads' in params:
            raise ValueError('请同时选择日期范围。')
        return path
    start, end = date_range(params)
    leads = None
    if 'leads' in params:
        text = params['leads']
        if not re.fullmatch(r'\d{1,3}(?:,\d{1,3}){0,168}', text):
            raise ValueError('预报时效列表不合法。')
        leads = set(map(int, text.split(',')))
        if any(lead > 168 for lead in leads):
            raise ValueError('预报时效超出范围。')
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
    if not re.fullmatch(r'\d{1,3}(?:,\d{1,3}){0,168}', text): raise ValueError('时效列表不合法。')
    leads = sorted(set(map(int, text.split(','))))
    if any(lead > 168 for lead in leads): raise ValueError('时效超出范围。')
    threshold = None
    if variable != 't':
        try: threshold = float(params.get('threshold', '.1'))
        except ValueError: raise ValueError('降水阈值不合法。') from None
        if threshold not in THRESHOLDS[variable]: raise ValueError('降水阈值不合法。')
    directory = snapshot()
    if directory is None or not (directory/'grid-map.sqlite').is_file():
        return dict(rows=[], state='empty')
    columns = list(MOMENTS) if variable == 't' else ['n'] + [f'{k}{GRID_THRESHOLDS.index(threshold)}' for k in ('h','m','f')]
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
        if variable != 't':
            expressions = ('COUNT(*),SUM(CASE WHEN fc>=:threshold AND ob>=:threshold THEN 1 ELSE 0 END),'
                           'SUM(CASE WHEN fc<:threshold AND ob>=:threshold THEN 1 ELSE 0 END),'
                           'SUM(CASE WHEN fc>=:threshold AND ob<:threshold THEN 1 ELSE 0 END)')
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
            result = moment_score(values) if variable == 't' else event_score(*map(int, values))
            result['n'] = int(result['n'])
            rows.append(dict(station=ident, model=model, var=variable, threshold=threshold, **result))
    return dict(rows=rows, model=model, var=variable, start=start, end=end, leads=leads, threshold=threshold,hour=hour,
                sample='地图合并所选日期、起报时次与时效的站点配对；CC为单站时间相关。主图连续量按时效/时刻等权，CC为平均站间空间相关；共同有效时次及单个时刻筛选仅用于主图。')


def dispatch(operation,query):
    allowed = {'meta':set(),'scores':{'var','models','days','range'},
               'station':{'id','var','model','lead','days','start','end','hour'},
               'series':{'station','var','model','lead','start','end','hour'},
               'grid-datasets':set(), 'grid-rows':{'dataset','start','end','generation','leads'},
               'map':{'model','var','threshold','start','end','lead','leads','station','hour'}}
    if operation not in allowed:
        raise KeyError(operation)
    params = parameters(query,allowed[operation])
    if operation == 'meta': return meta()
    if operation == 'scores': return scores(params)
    if operation == 'grid-datasets': return grid_datasets()
    if operation == 'grid-rows': return grid_rows(params)
    if operation == 'map': return map_scores(params)
    if operation == 'series':
        params['id'] = params.pop('station', '')
        params['model'] = next((key for key, value in GRID_MODELS.items() if value == params.get('model')), params.get('model', 'ec01'))
        date_range(params)
    return station(params)
