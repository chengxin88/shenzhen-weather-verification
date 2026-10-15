"""Public demo metadata and separate immutable WN3 monthly samples."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get('WN3_MODULE_DEMO_ROOT', str(Path(__file__).parent/'cache/wn3-module-demo')))
WN3 = 'Google WeatherNext 3（集合均值）'


def profiles():
    from model_interfaces import PRODUCTS
    # PANGU is one model in module selectors; its two product contracts stay separate.
    return [dict(id='wn3-demo:'+p.id, target_product_id=p.id, group=p.group,
                 label=p.label.replace(' 预报系统','').replace(' 预报',''),
                 source_model=WN3, is_demo=True, excluded_from_official_scores=True)
            for p in PRODUCTS if not p.existing_source and p.id!='pangu-typhoon']


def monthly_catalog():
    months=[]
    for path in sorted(ROOT.glob('????-??.json'), reverse=True):
        try:
            data = read_month(path.stem)
            months.append(dict(month=data['month'], generated=data['generated'], obs_days=data['obs']['days'],
                               forecast_days={w['id']:w['models'][WN3]['days'] for w in data['windows']}))
        except (ValueError, OSError, KeyError):
            continue
    return dict(months=months, is_demo=True, source_model=WN3, excluded_from_official_scores=True)


def catalog():
    from model_interfaces import GROUPS
    return dict(schema=1, profiles=profiles(), groups=GROUPS, source_model=WN3,
                generated=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                monthly=monthly_catalog(),
                unavailable={'circulation':'WN3 本地缓存没有等压面高空场，暂不生成环流演示评分。',
                             'typhoon':'WN3 本地缓存没有台风预报路径，暂不生成登陆和路径演示评分。'})


def read_month(month):
    if not isinstance(month,str) or not re.fullmatch(r'\d{4}-\d{2}',month):
        raise ValueError('请选择有效的演示月份。')
    path=ROOT/(month+'.json')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=(ROOT/(month+'.sha256')).read_text().strip():
        raise ValueError('WN3 月降水演示校验失败。')
    data=json.loads(raw)
    if (data.get('is_demo') is not True or data.get('excluded_from_official_scores') is not True
            or data.get('source_model')!=WN3 or data.get('month')!=month):
        raise ValueError('WN3 月降水演示身份不匹配。')
    return data
