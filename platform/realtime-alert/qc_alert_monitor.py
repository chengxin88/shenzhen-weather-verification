#!/usr/bin/env python3
"""新发布资料触发数据质控，并生成待推送的异常队列。

- 只读取各资料源的发布清单和质控页面报告，不修改原始资料、不下载、不检查历史资料。
- 发现新数据时调用平台现有的 quality_control.py --scan，结果写入页面使用的同一份报告。
- 本程序不发消息：异常写入 outbox，由 Mac 端 qc_alert_sender.py 拉取后私聊推送并确认。

用法：
  --initialize    部署前建立当前发布基线，不扫描、不处理旧报告
  --tick          定时器调用：检测新数据，必要时触发质控，处理最新报告
  --pending       输出待推送队列和运行状态（JSON 一行）
  --status        输出运行状态
  --today         输出北京时间当天的检查与异常统计（晚间汇总用）
  --today-cutoff-20 输出北京时间当天截至当前时间或 20:00 的检查与异常统计
  --ack ID...     确认已推送
  --enqueue-test  生成一条链路测试消息
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
BJT = timezone(timedelta(hours=8))
APP_DIR = Path(os.getenv('QC_ALERT_APP_DIR', '/home/chengyang/verification-test'))
WEATHER = Path(os.getenv('QC_WEATHER_ROOT', '/home/chengyang/gods-eye-weather/weather-data')) / 'region-v4'
APP = Path(os.getenv('QC_APP_ROOT', '/home/chengyang/ec-gfs-server'))
SNAPSHOT = Path(os.getenv('QC_ALERT_SNAPSHOT', str(APP_DIR / 'cache/quality-control/snapshot.json')))
STATE_DIR = Path(os.getenv('QC_ALERT_STATE', str(Path.home() / '.local/state/weather-qc-alert')))
SCAN_PYTHON = os.getenv('QC_ALERT_SCAN_PYTHON', '/home/chengyang/.venvs/ec-gfs-server/bin/python')
SCAN_TIMEOUT = int(os.getenv('QC_ALERT_SCAN_TIMEOUT', '240'))
MIN_GAP = int(os.getenv('QC_ALERT_MIN_GAP', '120'))        # 两次触发扫描的最小间隔（秒）
PERIODIC = int(os.getenv('QC_ALERT_PERIODIC', '3600'))     # 无新数据时也定期复查（捕捉到报延迟）
SKIP_SYSTEM = os.getenv('QC_ALERT_SKIP_SYSTEM') == '1'
SEEN_TTL = timedelta(hours=48)
BUSY_LIMIT = 6
FAIL_LIMIT = 3
KEEP_DAYS = 8
ALERT_SEVERITIES = ('error', 'warning')
# 近 7 天补下载队列属于历史资料，按用户要求不实时推送，仅在晚间汇总中说明。
EXCLUDED_CODES = {'backfill_short_backlog'}
JSON_LIMIT = 4_000_000
ID_RE = re.compile(r'^[0-9a-f]{16}$')
GUARD = ['/usr/bin/timeout', '8', '/usr/bin/python3', '/usr/local/libexec/weather-storage-guard.py', '--ready']
TIMER = 'weather-qc-alert.timer'


def now_utc():
    return datetime.now(UTC)


def iso(d):
    return d.astimezone(UTC).isoformat(timespec='seconds')


def bjt(d):
    return d.astimezone(BJT).isoformat(timespec='seconds')


def parse(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def load_json(path, default=None):
    try:
        if path.stat().st_size > JSON_LIMIT:
            raise ValueError('too large')
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.tmp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def append_jsonl(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(value, ensure_ascii=False) + '\n')


def read_jsonl(path, since=None):
    rows = []
    try:
        with path.open(encoding='utf-8') as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        continue
                    t = parse(row.get('time_utc'))
                except ValueError:
                    continue
                if since is not None and (t is None or t < since):
                    continue
                rows.append(row)
    except OSError:
        pass
    return rows


def prune_jsonl(path, now):
    try:
        if path.stat().st_size < 1_000_000:
            return
    except OSError:
        return
    keep = read_jsonl(path, since=now - timedelta(days=KEEP_DAYS))
    fd, name = tempfile.mkstemp(prefix='.tmp-', dir=path.parent)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        for row in keep:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    os.replace(name, path)


def normalize_state(value):
    problems = []
    if not isinstance(value, dict):
        value = {}
        problems.append('state')
    st = dict(value)
    for key in ('signatures', 'seen', 'health'):
        if key in st and not isinstance(st[key], dict):
            st.pop(key)
            problems.append(key)
    for key in ('consecutive_failures', 'busy_streak'):
        if key in st and (type(st[key]) is not int or st[key] < 0):
            st[key] = 0
            problems.append(key)
    for key in ('monitor_started_utc', 'last_tick_utc', 'last_scan_started_utc',
                'last_scan_ok_utc', 'last_scan_finished_utc', 'processed_generated_utc',
                'periodic_from_utc'):
        if key in st and parse(st[key]) is None:
            st.pop(key)
            problems.append(key)
    for key in ('active', 'state_recovery_issues'):
        if key in st and (not isinstance(st[key], list)
                          or not all(isinstance(x, dict if key == 'active' else str) for x in st[key])):
            st.pop(key)
            problems.append(key)
    if 'latest_summary' in st and not isinstance(st['latest_summary'], dict):
        st.pop('latest_summary')
        problems.append('latest_summary')
    for key, stamp in list(st.get('health', {}).items()):
        if parse(stamp) is None:
            st['health'].pop(key)
            problems.append(f'health.{key}')
    for fp, entry in list(st.get('seen', {}).items()):
        stamp = entry.get('alerted_utc') if isinstance(entry, dict) else entry
        severity = entry.get('severity') if isinstance(entry, dict) else None
        if parse(stamp) is None or (isinstance(entry, dict) and severity not in ALERT_SEVERITIES):
            st['seen'].pop(fp)
            problems.append(f'seen.{fp}')
    if problems:
        st['state_recovery_issues'] = sorted(set(st.get('state_recovery_issues', []) + problems))
    return st, problems


def load_state():
    path = STATE_DIR / 'state.json'
    return normalize_state(load_json(path, {} if not path.exists() else None))


# ---------------------------------------------------------------- 发布签名

def _manifest(path):
    d = load_json(path)
    if not isinstance(d, dict):
        raise ValueError('manifest')
    return d


def native_sig(slug):
    d = _manifest(WEATHER / slug / 'catalog.json')
    return '|'.join(str(x) for x in (d.get('run'), d.get('updated'), len(d.get('frames') or []),
                                     bool(d.get('partial')), len(d.get('errors') or [])))


def wn3_sig(now):
    root = WEATHER / 'weathernext3'
    base = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    for age in range(24):  # 与质控模块一致：逐小时探测最近 24 个批次清单
        run = base - timedelta(hours=age)
        p = root / f'run_{run:%Y%m%d%H}.json'
        if p.exists():
            d = _manifest(p)
            return f"{p.name}|{d.get('updated')}|{d.get('status')}|{len(d.get('frames') or [])}"
    d = _manifest(root / 'catalog.json')
    return f"catalog|{d.get('run')}|{d.get('updated')}|{len(d.get('frames') or [])}"


def wn2_sig():
    d = _manifest(APP / 'cache/weathernext2-prefetch-status.json')
    return f"{d.get('run_utc')}|{len(d.get('ready') or [])}|{len(d.get('errors') or [])}"


def obs_sig(now):
    root = APP / 'cache/OBSERVATION/grid'
    end = now.astimezone(BJT).replace(minute=0, second=0, microsecond=0)
    for age in range(25):
        p = root / f'{end - timedelta(hours=age):%Y%m%d%H}.pkl'
        if p.exists():
            return f'{p.name}|{p.stat().st_size}'
    return 'none'


def ens_sig():
    root = APP / 'cache/ECMWF-ENSEMBLES'
    runs = sorted(x.name for x in root.iterdir() if re.fullmatch(r'\d{10}', x.name) and (x / 'maps').is_dir())
    if not runs:
        return 'none'
    return f"{runs[-1]}|{sum(1 for _ in (root / runs[-1] / 'maps').iterdir())}"


def signatures(now):
    funcs = {
        'ecmwf': lambda: native_sig('ecmwf'), 'gfs': lambda: native_sig('gfs'),
        'aifs': lambda: native_sig('aifs'), 'aigfs': lambda: native_sig('aigfs'),
        'weathernext2': wn2_sig, 'weathernext3': lambda: wn3_sig(now),
        'observations': lambda: obs_sig(now), 'ecmwf-ensembles': ens_sig,
    }
    out = {}
    for key, func in funcs.items():
        try:
            out[key] = func()
        except Exception as exc:  # 清单不可读本身由质控扫描判定，这里只记录变化
            out[key] = f'unavailable:{type(exc).__name__}'
    return out


# ---------------------------------------------------------------- 扫描

def valid_snapshot(snap):
    if (not isinstance(snap, dict) or type(snap.get('schema')) is not int or snap['schema'] != 1
            or parse(snap.get('generated_utc')) is None
            or not isinstance(snap.get('summary'), dict)
            or not isinstance(snap.get('sources'), list) or not snap['sources']):
        return False
    if 'generated_bjt' in snap and parse(snap['generated_bjt']) is None:
        return False
    if any(type(snap['summary'][key]) is not int or snap['summary'][key] < 0
           for key in ('error', 'warning', 'sampled_files') if key in snap['summary']):
        return False
    return all(isinstance(source, dict)
               and all(isinstance(source.get(key), str) and source[key].strip()
                       for key in ('id', 'name'))
               and all(isinstance(source.get(key), list)
                       and all(isinstance(row, dict) for row in source[key])
                       for key in ('issues', 'metrics'))
               for source in snap['sources'])


def valid_generation(snap, st, now):
    generated = parse(snap.get('generated_utc'))
    started = parse(st.get('monitor_started_utc'))
    processed = parse(st.get('processed_generated_utc'))
    return (generated is not None and generated <= now + timedelta(minutes=5)
            and (started is None or generated >= started)
            and (processed is None or generated >= processed))


def initialize():
    state_path = STATE_DIR / 'state.json'
    if state_path.exists():
        return {'initialize': 'already_initialized'}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'tick.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if state_path.exists():
            return {'initialize': 'already_initialized'}
        now = now_utc()
        st = {'signatures': signatures(now), 'monitor_started_utc': iso(now),
              'last_scan_started_utc': iso(now), 'periodic_from_utc': iso(now)}
        snap = load_json(SNAPSHOT)
        if valid_snapshot(snap) and valid_generation(snap, {}, now):
            st['processed_generated_utc'] = snap['generated_utc']
        atomic_json(state_path, st)
    return {'initialize': 'initialized'}


def snapshot_generated():
    d = load_json(SNAPSHOT)
    return d.get('generated_utc') if isinstance(d, dict) else None


def scan_command():
    raw = os.getenv('QC_ALERT_SCAN_CMD')
    if raw:
        return json.loads(raw)
    return [SCAN_PYTHON, str(APP_DIR / 'quality_control.py'), '--scan', '--output', str(SNAPSHOT)]


def run_scan():
    before = snapshot_generated()
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
    started = now_utc()
    detail = ''
    try:
        p = subprocess.run(scan_command(), cwd=str(APP_DIR), env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=SCAN_TIMEOUT)
        code = p.returncode
        lines = p.stderr.decode('utf-8', 'replace').strip().splitlines()
        detail = lines[-1][:200] if lines else ''
    except subprocess.TimeoutExpired:
        code = 'timeout'
    except OSError as exc:
        code, detail = 'oserror', str(exc)[:200]
    snap = load_json(SNAPSHOT)
    after = snap.get('generated_utc') if isinstance(snap, dict) else None
    if code == 0 and (not valid_snapshot(snap)
                      or not valid_generation(snap, {'processed_generated_utc': before}, now_utc())):
        result, detail = 'failed', 'invalid snapshot'
    elif code == 0 and parse(after) != parse(before):
        result = 'ok'
    elif code == 0:
        result = 'busy'  # 页面发起的扫描正持有进程锁，下一轮再处理
    elif code == 'timeout':
        result = 'timeout'
    else:
        result = 'failed'
    return {'result': result, 'exit': code, 'started_utc': iso(started), 'finished_utc': iso(now_utc()),
            'generated_utc': after if result == 'ok' else None, 'detail': detail if result != 'ok' else ''}


# ---------------------------------------------------------------- 报告处理

def fingerprint(source_id, issue):
    key = '|'.join(str(issue.get(k)) for k in ('code', 'run_utc', 'lead_hours', 'field'))
    return hashlib.sha1(f'{source_id}|{key}'.encode()).hexdigest()[:16]


def metric_for(source, issue):
    if issue.get('field') is None:
        return None
    for m in source.get('metrics') or []:
        if (m.get('field') == issue['field'] and m.get('lead_hours') == issue.get('lead_hours')
                and (issue.get('run_utc') is None or m.get('run_utc') in (None, issue.get('run_utc')))):
            return {k: m.get(k) for k in ('min', 'max', 'unit', 'review_min', 'review_max', 'nonfinite_count',
                                          'range_count', 'context_range_count', 'checked_cells',
                                          'accumulation_hours', 'valid_bjt', 'review_context')}
    return None


def alert_items(snap):
    items = []
    for s in snap.get('sources') or []:
        for i in s.get('issues') or []:
            if i.get('severity') not in ALERT_SEVERITIES or i.get('code') in EXCLUDED_CODES:
                continue
            items.append({'fp': fingerprint(s.get('id'), i), 'source_id': s.get('id'), 'source': s.get('name'),
                          'severity': i.get('severity'), 'code': i.get('code'), 'message': i.get('message'),
                          'run_utc': i.get('run_utc'), 'run_bjt': i.get('run_bjt'),
                          'lead_hours': i.get('lead_hours'), 'field': i.get('field'), 'metric': metric_for(s, i)})
    return items


def brief(item):
    return {k: item.get(k) for k in ('source', 'severity', 'code', 'field', 'lead_hours', 'run_bjt')}


def enqueue(kind, payload, now):
    body = {'kind': kind, 'created_utc': iso(now), 'created_bjt': bjt(now), **payload}
    ident = hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    body['id'] = ident
    atomic_json(STATE_DIR / 'outbox' / f'{ident}.json', body)
    items = payload.get('items') or []
    append_jsonl(STATE_DIR / 'events.jsonl', {
        'time_utc': iso(now), 'event': 'enqueued', 'kind': kind, 'id': ident,
        'condition': payload.get('condition'), 'active': payload.get('active'),
        'error': sum(x.get('severity') == 'error' for x in items),
        'warning': sum(x.get('severity') == 'warning' for x in items),
        'items': [brief(x) for x in items[:40]]})
    return ident


def recent_seen(seen, now):
    out = {}
    for fp, entry in seen.items():
        stamp = entry.get('alerted_utc') if isinstance(entry, dict) else entry
        alerted = parse(stamp)
        if alerted is not None and now - SEEN_TTL < alerted <= now:
            out[fp] = entry
    return out


def process_snapshot(st, now, ours=None):
    snap = load_json(SNAPSHOT)
    if not valid_snapshot(snap) or not valid_generation(snap, st, now):
        return None
    gen = snap['generated_utc']
    if parse(gen) == parse(st.get('processed_generated_utc')):
        return None
    normalized, _ = normalize_state(st)
    unique = {}
    for item in alert_items(snap):
        previous = unique.get(item['fp'])
        if previous is None or item['severity'] == 'error':
            unique[item['fp']] = item
    items = list(unique.values())
    # 仅报告中存在的源能确认恢复；缺失源及无法归属的旧版记录保留到期。
    source_ids = {source['id'] for source in snap['sources']}
    seen = {fp: entry for fp, entry in recent_seen(normalized.get('seen', {}), now).items()
            if fp in unique or not isinstance(entry, dict) or entry.get('source_id') not in source_ids}
    new = []
    for item in items:
        previous = seen.get(item['fp'])
        severity = previous.get('severity') if isinstance(previous, dict) else None
        if previous is None or (item['severity'] == 'error' and severity != 'error'):
            new.append(item)
    if new:
        mine = bool(ours) and ours.get('generated_utc') == gen
        enqueue('data', {'generated_utc': gen, 'generated_bjt': snap.get('generated_bjt'),
                         'trigger': ours.get('trigger') if mine else 'external',
                         'changed': ours.get('changed', []) if mine else [],
                         'items': new, 'summary': snap['summary']}, now)
        for item in new:
            seen[item['fp']] = {'alerted_utc': iso(now), 'severity': item['severity'],
                                'source_id': item['source_id']}
    # 严重级别反映最近观察，时间仍保留最近告警；再次由 warning 升 error 也要告警。
    for item in items:
        entry = seen.get(item['fp'])
        if isinstance(entry, dict):
            seen[item['fp']] = {**entry, 'severity': item['severity'], 'source_id': item['source_id']}
    normalized.update(seen=seen, active=[brief(x) for x in items][:200],
                      processed_generated_utc=gen, processed_generated_bjt=snap.get('generated_bjt'),
                      latest_summary=snap['summary'])
    st.clear()
    st.update(normalized)
    return len(new)


def update_health(st, now):
    health = st.setdefault('health', {})
    failures = st.get('consecutive_failures', 0)
    if failures >= FAIL_LIMIT and not health.get('scan_failing'):
        enqueue('health', {'condition': 'scan_failing', 'active': True, 'failures': failures,
                           'last_result': st.get('last_scan_result'),
                           'last_ok_utc': st.get('last_scan_ok_utc')}, now)
        health['scan_failing'] = iso(now)
    elif failures == 0 and health.get('scan_failing'):
        enqueue('health', {'condition': 'scan_failing', 'active': False, 'since_utc': health['scan_failing'],
                           'last_ok_utc': st.get('last_scan_ok_utc')}, now)
        health.pop('scan_failing')


def tick():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'tick.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'tick': 'locked'}
        now = now_utc()
        state_path = STATE_DIR / 'state.json'
        st, problems = load_state()
        if problems:
            st['state_recovered_utc'] = iso(now)
            append_jsonl(STATE_DIR / 'events.jsonl', {
                'time_utc': iso(now), 'event': 'state_recovered', 'issues': problems})
        st.setdefault('monitor_started_utc', iso(now))
        st['last_tick_utc'] = iso(now)
        sig = signatures(now)
        old = st.get('signatures')
        changed = [] if old is None else [k for k in sig if sig[k] != old.get(k)]
        last_start = parse(st.get('last_scan_started_utc'))
        periodic_from = parse(st.get('last_scan_ok_utc')) or parse(st.get('periodic_from_utc'))
        gap_ok = last_start is None or (now - last_start).total_seconds() >= MIN_GAP
        periodic = periodic_from is None or (now - periodic_from).total_seconds() >= PERIODIC
        ours = None
        if gap_ok and (old is None or changed or periodic):
            trigger = 'initial' if old is None else ('new_data' if changed else 'periodic')
            st['last_scan_started_utc'] = iso(now)
            atomic_json(state_path, st)  # 先记下启动时间，异常退出也不会连续重扫
            before = snapshot_generated()
            ours = run_scan()
            if ours['result'] == 'ok':
                snap = load_json(SNAPSHOT)
                if (not valid_snapshot(snap) or not valid_generation(snap, st, now_utc())
                        or not valid_generation(snap, {'processed_generated_utc': before}, now_utc())
                        or parse(ours.get('generated_utc')) != parse(snap.get('generated_utc'))):
                    ours.update(result='failed', generated_utc=None, detail='invalid snapshot')
                elif parse(snap['generated_utc']) == parse(before):
                    ours.update(result='busy', generated_utc=None, detail='unchanged snapshot')
                else:
                    ours['summary'] = snap['summary']
            ours.update(trigger=trigger, changed=changed, time_utc=ours['finished_utc'])
            if ours['result'] == 'ok':
                st['signatures'] = sig  # 扫描期间又到的新数据会在下一轮触发
                st['last_scan_ok_utc'] = ours['finished_utc']
                st['periodic_from_utc'] = ours['finished_utc']
                st['consecutive_failures'] = 0
                st['busy_streak'] = 0
            elif ours['result'] == 'busy':
                st['busy_streak'] = st.get('busy_streak', 0) + 1
                if st['busy_streak'] >= BUSY_LIMIT:
                    st['consecutive_failures'] = st.get('consecutive_failures', 0) + 1
            else:
                st['consecutive_failures'] = st.get('consecutive_failures', 0) + 1
            st['last_scan_result'] = ours['result']
            st['last_scan_finished_utc'] = ours['finished_utc']
        new_alerts = process_snapshot(st, now_utc(), ours)
        if ours is not None:
            ours['new_alerts'] = new_alerts or 0
            append_jsonl(STATE_DIR / 'scans.jsonl', ours)
        update_health(st, now_utc())
        st['seen'] = recent_seen(st.get('seen', {}), now)
        atomic_json(state_path, st)
        for name in ('scans.jsonl', 'events.jsonl'):
            prune_jsonl(STATE_DIR / name, now)
        sent = STATE_DIR / 'sent'
        if sent.is_dir():
            for p in sent.glob('*.json'):
                if p.stat().st_mtime < (now - timedelta(days=KEEP_DAYS)).timestamp():
                    p.unlink(missing_ok=True)
        return {'tick': 'done', 'changed': changed, 'scan': ours and ours['result'],
                'new_alerts': new_alerts, 'pending': len(list((STATE_DIR / 'outbox').glob('*.json')))}


# ---------------------------------------------------------------- 查询与确认

def _system(cmd):
    if SKIP_SYSTEM:
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None


def pending_items():
    rows = [load_json(p) for p in sorted((STATE_DIR / 'outbox').glob('*.json'))]
    return sorted([r for r in rows if isinstance(r, dict)], key=lambda r: r.get('created_utc', ''))


def status():
    now = now_utc()
    st, _ = load_state()
    tick_at = parse(st.get('last_tick_utc'))
    timer = _system(['systemctl', '--user', 'is-active', TIMER])
    guard = _system(GUARD)
    active = st.get('active') or []
    return {
        'now_utc': iso(now), 'now_bjt': bjt(now),
        'monitor_started_utc': st.get('monitor_started_utc'),
        'last_tick_utc': st.get('last_tick_utc'),
        'tick_age_seconds': int((now - tick_at).total_seconds()) if tick_at else None,
        'timer': (timer.stdout.strip() or 'unknown') if timer else ('skipped' if SKIP_SYSTEM else 'unknown'),
        'storage_ready': (guard.returncode == 0) if guard else None,
        'last_scan_result': st.get('last_scan_result'),
        'last_scan_ok_utc': st.get('last_scan_ok_utc'),
        'consecutive_failures': st.get('consecutive_failures', 0),
        'busy_streak': st.get('busy_streak', 0),
        'state_recovery_issues': st.get('state_recovery_issues', []),
        'state_recovered_utc': st.get('state_recovered_utc'),
        'processed_generated_bjt': st.get('processed_generated_bjt'),
        'latest_summary': st.get('latest_summary'),
        'active_count': len(active), 'active': active[:30],
        'pending_count': len(list((STATE_DIR / 'outbox').glob('*.json'))),
    }


def ack(ids):
    done = 0
    for ident in ids:
        if not ID_RE.fullmatch(ident):
            raise SystemExit(f'invalid id: {ident!r}')
        src = STATE_DIR / 'outbox' / f'{ident}.json'
        body = load_json(src)
        if not isinstance(body, dict):
            continue
        body['acked_utc'] = iso(now_utc())
        atomic_json(STATE_DIR / 'sent' / f'{ident}.json', body)
        src.unlink(missing_ok=True)
        append_jsonl(STATE_DIR / 'events.jsonl', {'time_utc': iso(now_utc()), 'event': 'acked', 'id': ident})
        done += 1
    return {'acked': done}


def today(cutoff_hour=None):
    now = now_utc()
    start = now.astimezone(BJT).replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = now if cutoff_hour is None else min(now, start.replace(hour=cutoff_hour))
    scans = [s for s in read_jsonl(STATE_DIR / 'scans.jsonl', since=start)
             if parse(s.get('time_utc')) <= window_end]
    events = [e for e in read_jsonl(STATE_DIR / 'events.jsonl', since=start)
              if parse(e.get('time_utc')) <= window_end and e.get('event') == 'enqueued']
    data = [e for e in events if e.get('kind') == 'data']
    ok = [s for s in scans if s.get('result') == 'ok']
    new_data_ok = [s for s in ok if s.get('trigger') == 'new_data' and s.get('changed')]
    snap = load_json(SNAPSHOT)
    valid = valid_snapshot(snap)
    generated = parse(snap['generated_utc']) if valid else None
    fresh = (generated is not None and start <= generated <= window_end
             and (now - generated).total_seconds() <= PERIODIC)
    current = snap if fresh else {}
    monitor_status = status()
    tick_age = monitor_status['tick_age_seconds']
    monitor_healthy = None if tick_age is None else (
        0 <= tick_age <= MIN_GAP + SCAN_TIMEOUT
        and monitor_status['last_scan_result'] == 'ok'
        and monitor_status['consecutive_failures'] == 0
        and monitor_status['busy_streak'] == 0
        and not monitor_status['state_recovery_issues']
        and monitor_status['timer'] in ('active', 'skipped')
        and monitor_status['storage_ready'] is not False)
    info = []
    for s in current.get('sources', []):
        for i in s.get('issues') or []:
            if i.get('severity') == 'info' or i.get('code') in EXCLUDED_CODES:
                info.append({'source': s.get('name'), 'code': i.get('code'), 'severity': i.get('severity'),
                             'message': i.get('message')})
    return {
        'window_start_bjt': bjt(start), 'window_end_bjt': bjt(window_end),
        'now_bjt': bjt(now), 'status': monitor_status,
        'check_completed': bool(fresh),
        'new_data_check_completed': bool(new_data_ok),
        'monitor_healthy': monitor_healthy,
        'scans': {'total': len(scans), 'ok': len(ok), 'ok_new_data': len(new_data_ok),
                  'sampled_files_total': sum(s['summary'].get('sampled_files', 0) for s in scans
                                             if isinstance(s.get('summary'), dict)),
                  'busy': sum(s.get('result') == 'busy' for s in scans),
                  'failed': sum(s.get('result') in ('failed', 'timeout') for s in scans),
                  'first_utc': scans[0].get('started_utc') if scans else None,
                  'last_ok_utc': ok[-1].get('finished_utc') if ok else None},
        'new_data_sources': sorted({k for s in new_data_ok for k in s.get('changed') or []}),
        'alerts': {'events': len(data), 'error': sum(e.get('error', 0) for e in data),
                   'warning': sum(e.get('warning', 0) for e in data),
                   'items': [x for e in data for x in e.get('items') or []][:40],
                   'health': [{'condition': e.get('condition'), 'active': e.get('active'), 'time_utc': e.get('time_utc')}
                              for e in events if e.get('kind') == 'health']},
        'snapshot': {'generated_bjt': snap.get('generated_bjt') if isinstance(snap, dict) else None,
                     'valid': valid, 'fresh': bool(fresh), 'summary': current.get('summary'), 'info': info[:20]},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--initialize', action='store_true')
    group.add_argument('--tick', action='store_true')
    group.add_argument('--pending', action='store_true')
    group.add_argument('--status', action='store_true')
    group.add_argument('--today', action='store_true')
    group.add_argument('--today-cutoff-20', action='store_true')
    group.add_argument('--ack', nargs='+', metavar='ID')
    group.add_argument('--enqueue-test', action='store_true')
    args = parser.parse_args(argv)
    if args.initialize:
        out = initialize()
    elif args.tick:
        out = tick()
    elif args.pending:
        out = {'pending': pending_items(), 'status': status()}
    elif args.status:
        out = status()
    elif args.today:
        out = today()
    elif args.today_cutoff_20:
        out = today(cutoff_hour=20)
    elif args.ack:
        out = ack(args.ack)
    else:
        out = {'id': enqueue('test', {'note': '链路测试'}, now_utc())}
    print(json.dumps(out, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
