"""Immutable WN3 sample, served only through explicitly separate demo routes."""
import hashlib
import json
import os
from pathlib import Path
import re

DEMO_ROOT = Path(os.getenv('MODEL_INTERFACE_DEMO_ROOT', str(Path(__file__).parent/'cache/model-interface-demo')))
MODEL = 'Google WeatherNext 3（集合均值）'
FILES = {'temperature_2m': 'temperature_2m.json', 'precipitation': 'precipitation.json', 'pairs': 'pairs.json'}


def manifest():
    value = json.loads((DEMO_ROOT/'current.json').read_text(encoding='utf-8'))
    if (value.get('schema') != 1 or value.get('is_demo') is not True
            or value.get('source_model') != MODEL or value.get('excluded_from_official_scores') is not True
            or not re.fullmatch(r'\d{10}_f\d{3}', str(value.get('sample_id', '')))):
        raise ValueError('演示资料身份不合法。')
    return value


def summary():
    try:
        value = manifest()
        allowed = ('schema', 'is_demo', 'source_model', 'excluded_from_official_scores', 'sample_id',
                   'run_utc', 'run_bjt', 'valid_utc', 'valid_bjt', 'lead_minutes', 'grid_degrees',
                   'interval_minutes', 'bounds', 'fields', 'scores', 'created_bjt')
        return {key: value[key] for key in allowed}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def read_data(value, variable):
    if variable not in FILES:
        raise ValueError('演示没有该字段。')
    raw = (DEMO_ROOT/value['sample_id']/FILES[variable]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != value['file_sha256'][variable]:
        raise ValueError('演示资料校验失败。')
    return json.loads(raw)


def field(request):
    from model_interfaces import product, validate_field, utc
    if product(request.product_id).group == 'typhoon':
        raise ValueError('台风路径接口需要真实路径样例。')
    value = manifest()
    specification = next((f for f in value['fields'] if f['variable'] == request.variable), None)
    if (specification is None or utc(request.run_utc) != utc(value['run_utc'])
            or request.lead_minutes != value['lead_minutes'] or request.level_hpa is not None
            or request.accumulation_minutes != specification['accumulation_minutes']):
        raise ValueError('请使用此批 WN3 演示的真实起报、时效与累计窗口。')
    payload = read_data(value, request.variable)
    payload['product_id'] = request.product_id
    result = validate_field(payload, request)
    result.update(is_demo=True, source_model=MODEL, excluded_from_official_scores=True,
                  sample_id=value['sample_id'], native_grid_degrees=value['grid_degrees'],
                  target_product_id=request.product_id,
                  note='WN3真实缓存的接口演示资料，不是目标模式预报；保留原生网格和真实小时场。')
    return result


def detail():
    value = manifest()
    brief = summary()
    if brief is None:
        raise ValueError('演示清单不完整。')
    return {**brief, 'pairs': read_data(value, 'pairs')}
