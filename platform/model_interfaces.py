"""Reserved product contracts. Only trusted, server-side adapters may supply data.

No downloads, schedules, credentials, sample scores, or rolling-run fallback are
created by this module. Product IDs are stable across future data-source changes.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import json
from pathlib import Path
from typing import Protocol

PREFIX = '/Verification/test/api/model-interfaces'
GROUPS = {'nowcasting': '短临预报系统', 'short_range': '短期预报系统', 'typhoon': '台风预报系统'}
UNITS = {'precipitation': 'mm', 'temperature_2m': 'degC', 'u10': 'm/s',
         'v10': 'm/s', 'pressure_msl': 'hPa', 'geopotential_height': 'm',
         'u': 'm/s', 'v': 'm/s'}


@dataclass(frozen=True)
class Product:
    id: str
    label: str
    group: str
    resolution_m: int | None = None
    existing_source: str | None = None


PRODUCTS = (
    Product('cma-gd-ec-mt-500m', 'CMA-GD(EC-MT) 500m 预报系统', 'nowcasting', 500),
    Product('cma-gd-ec-mt-1km', 'CMA-GD(EC-MT) 1km 预报系统', 'nowcasting', 1000),
    Product('feed-qpf', 'FEED QPF 短临预报', 'nowcasting'),
    Product('unicast', 'UNICAST 预报系统', 'nowcasting'),
    Product('cma-gd-ggf-mt-3km', 'CMA-GD(GGF-MT) 3km 预报系统', 'short_range', 3000),
    Product('cma-gd-ec-mt-3km', 'CMA-GD(EC-MT) 3km 预报系统', 'short_range', 3000),
    Product('cma-gd-3km', 'CMA-GD 3km 预报系统', 'short_range', 3000),
    Product('cma-meso-3km', 'CMA-MESO 3km 预报系统', 'short_range', 3000),
    Product('feed-short-range', 'FEED 短期预报', 'short_range'),
    Product('pangu-short-range', 'PANGU', 'short_range'),
    Product('zhiji', 'ZHIJI', 'short_range'),
    Product('cma-trams', 'CMA-TRAMS', 'typhoon'),
    Product('ecmwf-typhoon', 'ECMWF', 'typhoon', existing_source='ECMWF-IFS'),
    Product('pangu-typhoon', 'PANGU', 'typhoon'),
)
_PRODUCTS = {p.id: p for p in PRODUCTS}
_ADAPTERS = {}
UI_REFERENCE_PATH = Path(__file__).with_name('model_ui_reference.json')


def ui_reference():
    """Read operator-reviewed observations; never use them to start downloads."""
    if not UI_REFERENCE_PATH.is_file():
        return None
    data = json.loads(UI_REFERENCE_PATH.read_text(encoding='utf-8'))
    for row in data['models']:
        for field in row['fields']:
            values = set()
            for segment in field['lead_segments']:
                start, end, step = segment['start'], segment['end'], segment['step']
                values.update([start] if step is None else range(start, end + 1, step))
            field['lead_minutes'] = sorted(values)
    return data


def product_ui_reference(product_id):
    reference = ui_reference()
    if reference is None:
        return None
    return {'observed_at': reference['observed_at'], 'source': reference['source'],
            'timezone': reference['timezone'],
            'models': [row for row in reference['models'] if product_id in row.get('candidate_product_ids', [])],
            'note': '业务页面参数参考；版本映射和原始资料能力仍需样例核实。'}


class InterfacePending(RuntimeError):
    pass


def product(product_id):
    try:
        return _PRODUCTS[product_id]
    except (KeyError, TypeError):
        raise ValueError('未知的模式产品。') from None


def catalog():
    from wn3_demo import summary as demo_summary
    demo = demo_summary()
    entries = []
    for p in PRODUCTS:
        state = 'existing' if p.existing_source else ('adapter_registered' if p.id in _ADAPTERS else 'reserved')
        entries.append(dict(id=p.id, label=p.label, group=p.group,
                            status=state, nominal_resolution_m=p.resolution_m,
                            existing_source=p.existing_source,
                            contract_url=f'{PREFIX}/{p.id}/contract',
                            demo_available=bool(demo) and p.group != 'typhoon',
                            demo_endpoint=f'{PREFIX}/{p.id}/demo-field' if p.group != 'typhoon' else None))
    return {'schema': 1, 'groups': GROUPS, 'products': entries, 'ui_reference': ui_reference(), 'demo': demo}


def contract(product_id):
    p = product(product_id)
    common = dict(schema=1, product_id=p.id, label=p.label, group=p.group,
                  status=next(x['status'] for x in catalog()['products'] if x['id'] == p.id),
                  nominal_resolution_m=p.resolution_m,
                  source=None, native_projection=None, supported_variables=None,
                  run_schedule_utc=None, lead_minutes=None,
                  required_source_details=['数据访问方式或服务器本地目录', '一个真实起报批次的样例文件',
                                           '变量名、单位、缺测值和投影', '起报频率、时效间隔及最长时效'])
    common['ui_reference'] = product_ui_reference(p.id)
    if p.existing_source:
        common.update(existing_source=p.existing_source,
                      existing_ui='/Verification/test/#typhoon',
                      existing_catalog='/Verification/test/api/typhoon/catalog',
                      existing_score='/Verification/test/api/typhoon/score',
                      note='复用现有 ECMWF-IFS 台风检验，不创建重复下载任务。')
    elif p.group == 'typhoon':
        common.update(endpoint=f'{PREFIX}/{p.id}/track',
                      request={'run_utc': '带 Z 或 +00:00 的 ISO8601 起报时间', 'storm_id': '真实台风编号'},
                      response={'product_id': p.id, 'run_utc': 'UTC 起报时间', 'storm_id': '台风编号',
                                'source': '实际资料来源（不含凭据）',
                                'wind_convention': '有风速时必须说明平均时段或模式瞬时风',
                                'points': [{'lead_minutes': '非负整数', 'valid_utc': '起报加时效',
                                            'lon': '度', 'lat': '度', 'pressure_hpa': 'hPa，可缺测',
                                            'wind_ms': 'm/s，可缺测'}]},
                      note='按真实模式路径接入；路径与强度缺测不填补，不裁掉评分区外的台风路径。')
    else:
        common.update(endpoint=f'{PREFIX}/{p.id}/field',
                      request={'run_utc': '带 Z 或 +00:00 的 ISO8601 起报时间',
                               'lead_minutes': '非负整数；支持分钟级短临产品',
                               'variable': list(UNITS),
                               'accumulation_minutes': '仅 precipitation 必填，正整数且不超过时效',
                               'level_hpa': '仅 u/v/geopotential_height 必填，正整数'},
                      response={'product_id': p.id, 'run_utc': 'UTC 起报时间', 'valid_utc': '起报加时效',
                                'variable': '与请求完全一致', 'units': UNITS,
                                'level_hpa': '与请求一致', 'source': '实际资料来源（不含凭据）',
                                'lon': '严格递增的一维经度轴，单位度', 'lat': '严格递增的一维纬度轴，单位度',
                                'values': '二维数组 [lat][lon]，缺测使用 null',
                                'accumulation_minutes': '降水累计长度；其他变量为 null',
                                'interval_start_utc': '降水有效时间减累计长度；其他变量为 null'},
                      retention_target={'wide': {'bounds': [60, 0, 150, 60], 'days': 7},
                                        'scoring': {'bounds': [108, 18, 120, 27], 'days': 30}},
                      note='变量列表为接入协议允许项，实际可用要素以源资料核实结果为准。保留原生分辨率及真实覆盖，超出覆盖处缺测。')
    return common


def utc(value):
    if not isinstance(value, str):
        raise ValueError('时间必须为 UTC ISO8601 字符串。')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('时间必须为 UTC ISO8601 字符串。') from None
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError('时间必须显式使用 UTC（Z 或 +00:00）。')
    return stamp.astimezone(timezone.utc)


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{label}必须为不小于 {minimum} 的整数。')
    return value


def lead_minutes_from_valid_time(run_utc, valid_utc):
    """Derive physical lead from timestamps, not a plot's slot-number label."""
    seconds = (utc(valid_utc) - utc(run_utc)).total_seconds()
    if seconds < 0 or seconds % 60:
        raise ValueError('有效时间必须是起报后整数分钟。')
    return int(seconds // 60)


@dataclass(frozen=True)
class GridRequest:
    product_id: str
    run_utc: str
    lead_minutes: int
    variable: str
    accumulation_minutes: int | None = None
    level_hpa: int | None = None

    def __post_init__(self):
        p = product(self.product_id)
        if p.group == 'typhoon':
            raise ValueError('台风产品使用路径接口。')
        run = utc(self.run_utc)
        integer(self.lead_minutes, '预报时效')
        try:
            run + timedelta(minutes=self.lead_minutes)
        except (OverflowError, ValueError):
            raise ValueError('预报时效超出时间范围。') from None
        if self.variable not in UNITS:
            raise ValueError('未知的接入变量。')
        if self.variable == 'precipitation':
            integer(self.accumulation_minutes, '降水累计长度', 1)
            if self.accumulation_minutes > self.lead_minutes:
                raise ValueError('降水累计时段不能跨越起报时刻。')
        elif self.accumulation_minutes is not None:
            raise ValueError('非降水要素不能指定降水累计长度。')
        if self.variable in ('u', 'v', 'geopotential_height'):
            integer(self.level_hpa, '气压层', 1)
        elif self.level_hpa is not None:
            raise ValueError('地面要素不能指定等压面层。')


@dataclass(frozen=True)
class TrackRequest:
    product_id: str
    run_utc: str
    storm_id: str

    def __post_init__(self):
        p = product(self.product_id)
        if p.group != 'typhoon' or p.existing_source:
            raise ValueError('此产品不使用预留路径接口；ECMWF 请使用现有台风检验。')
        utc(self.run_utc)
        if not isinstance(self.storm_id, str) or not self.storm_id.strip() or len(self.storm_id) > 64:
            raise ValueError('请指定真实台风编号。')


class GridAdapter(Protocol):
    def fetch_field(self, request: GridRequest) -> dict: ...


class TrackAdapter(Protocol):
    def fetch_track(self, request: TrackRequest) -> dict: ...


def register_adapter(product_id, adapter):
    """Call only from trusted application startup code, never from HTTP input."""
    p = product(product_id)
    if p.existing_source:
        raise ValueError('已有来源通过现有检验入口复用。')
    method = 'fetch_track' if p.group == 'typhoon' else 'fetch_field'
    if not callable(getattr(adapter, method, None)):
        raise ValueError(f'适配器必须实现 {method}。')
    if product_id in _ADAPTERS:
        raise ValueError('该产品已有适配器，不能重复绑定。')
    _ADAPTERS[product_id] = adapter


def _source(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('source'), str) or not payload['source'].strip():
        raise ValueError('模式输出必须标明实际资料来源。')


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_field(payload, request):
    _source(payload)
    if payload.get('is_demo'):
        raise ValueError('演示资料不能进入正式模式数据通道。')
    run = utc(request.run_utc)
    valid = run + timedelta(minutes=request.lead_minutes)
    if (payload.get('product_id') != request.product_id or utc(payload.get('run_utc')) != run
            or utc(payload.get('valid_utc')) != valid or payload.get('variable') != request.variable
            or payload.get('units') != UNITS[request.variable]
            or payload.get('level_hpa') != request.level_hpa
            or payload.get('accumulation_minutes') != request.accumulation_minutes):
        raise ValueError('模式、起报、有效时间、变量、层次或单位不匹配。')
    if request.variable == 'precipitation':
        if utc(payload.get('interval_start_utc')) != valid - timedelta(minutes=request.accumulation_minutes):
            raise ValueError('降水累计起止时间不匹配。')
    elif payload.get('interval_start_utc') is not None:
        raise ValueError('非降水要素不能指定降水累计时段。')
    axes = []
    for key, limit in [('lon', 180), ('lat', 90)]:
        axis = payload.get(key)
        if not isinstance(axis, list) or not axis or any(not _number(v) or abs(v) > limit for v in axis):
            raise ValueError('经纬度轴缺失或不合法。')
        if any(a >= b for a, b in zip(axis, axis[1:])):
            raise ValueError('经纬度轴必须严格递增。')
        axes.append(axis)
    lon, lat = axes
    values = payload.get('values')
    if not isinstance(values, list) or len(values) != len(lat):
        raise ValueError('格点数组必须为 [lat][lon]。')
    for row in values:
        if not isinstance(row, list) or len(row) != len(lon) or any(v is not None and not _number(v) for v in row):
            raise ValueError('格点维度不匹配或含非有限值；缺测请用 null。')
    return {key: payload.get(key) for key in ('product_id', 'run_utc', 'valid_utc', 'variable',
            'units', 'level_hpa', 'source', 'lon', 'lat', 'values',
            'accumulation_minutes', 'interval_start_utc')}


def validate_track(payload, request):
    _source(payload)
    run = utc(request.run_utc)
    if payload.get('product_id') != request.product_id or utc(payload.get('run_utc')) != run or payload.get('storm_id') != request.storm_id:
        raise ValueError('台风产品、编号或起报时刻不匹配。')
    points = payload.get('points')
    if not isinstance(points, list) or not points:
        raise ValueError('路径缺失。')
    previous = -1
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('路径点必须为对象。')
        lead = integer(point.get('lead_minutes'), '路径时效')
        if lead <= previous:
            raise ValueError('路径时效必须严格递增。')
        try:
            valid = run + timedelta(minutes=lead)
        except OverflowError:
            raise ValueError('路径时效超出时间范围。') from None
        if utc(point.get('valid_utc')) != valid:
            raise ValueError('路径有效时刻与时效不一致。')
        if not _number(point.get('lon')) or abs(point['lon']) > 180 or not _number(point.get('lat')) or abs(point['lat']) > 90:
            raise ValueError('路径经纬度不合法。')
        for key in ('pressure_hpa', 'wind_ms'):
            value = point.get(key)
            if value is not None and (not _number(value) or value < 0):
                raise ValueError('台风强度数值或单位不合法。')
        if point.get('wind_ms') is not None and (not isinstance(payload.get('wind_convention'), str) or not payload['wind_convention'].strip()):
            raise ValueError('提供风速时必须说明风速平均时段或模式瞬时风口径。')
        previous = lead
    result = {key: payload.get(key) for key in ('product_id', 'run_utc', 'storm_id', 'source', 'wind_convention')}
    result['points'] = [{key: point.get(key) for key in ('lead_minutes', 'valid_utc', 'lon', 'lat', 'pressure_hpa', 'wind_ms')}
                        for point in points]
    return result


def fetch(request):
    p = product(request.product_id)
    adapter = _ADAPTERS.get(p.id)
    if adapter is None:
        raise InterfacePending(f'{p.label}已预留接口，等待真实数据源。')
    if isinstance(request, GridRequest):
        return validate_field(adapter.fetch_field(request), request)
    if isinstance(request, TrackRequest):
        return validate_track(adapter.fetch_track(request), request)
    raise ValueError('请求类型不合法。')
