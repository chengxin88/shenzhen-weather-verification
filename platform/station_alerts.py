"""WeCom alerts for station observation outages, sent at the end of a refresh.

Enabled only when STATIONS_ALERTS=1 (set by weather-stations.service), so tests
and manual runs stay silent. One message per problem, repeated at most every
REPEAT while it lasts, plus one recovery message. Never raises to the caller.
"""
from datetime import timedelta
from pathlib import Path
import os
import shlex
import subprocess
from station_common import atomic_json, iso, now, read_json, stamp

STALE = timedelta(hours=12)
REPEAT = timedelta(hours=24)
SENDER = Path.home() / 'wecom-claude/wecom-send'
LABELS = {'latch': 'OGIMET 停止标记', 'stale': 'OGIMET 实况断档'}


def command():
    configured = os.environ.get('STATIONS_ALERT_CMD')
    if configured:
        return shlex.split(configured)
    return [str(SENDER), '--text'] if SENDER.is_file() else None


def send(text):
    cmd = command()
    if not cmd:
        print('Station alert (no sender):', text, flush=True); return False
    try:
        result = subprocess.run(cmd + [text], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        print('Station alert send failed:', exc, flush=True); return False
    if result.returncode:
        print('Station alert send failed:', result.returncode, result.stdout[-200:], result.stderr[-200:], flush=True)
    return result.returncode == 0


def problems(root, at):
    found = {}
    stopped = read_json(root / 'ogimet-journal.json', {}).get('stopped')
    if stopped:
        found['latch'] = (stopped, f'OGIMET 已锁住，不再抓取 SYNOP：{stopped}。'
                          f'检查 {root}/ogimet-journal.json 和 raw/ogimet/*.error 后人工清除。')
    latest = read_json(root / 'obs-status.json', {}).get('sources', {}).get('ogimet', {}).get('latest')
    if latest and at - stamp(latest) > STALE:
        hours = int((at - stamp(latest)).total_seconds() // 3600)
        found['stale'] = ('stale', f'OGIMET SYNOP 实况最新只到 {latest}（{hours} 小时未更新），'
                          '站点地图最近几天会大面积变灰。')
    return found


def check(root, at=None):
    at = at or now(); path = root / 'alert-state.json'
    state = read_json(path, {}); current = problems(root, at); lines = []; updated = dict(state)
    for key, (detail, text) in current.items():
        previous = state.get(key)
        if previous and previous.get('detail') == detail and at - stamp(previous['sent']) < REPEAT:
            continue
        lines.append(text); updated[key] = dict(detail=detail, sent=iso(at))
    for key in [k for k in state if k not in current]:
        lines.append(f'已恢复：{LABELS.get(key, key)}。'); updated.pop(key)
    if not lines:
        return None
    message = '【站点检验】' + '\n'.join(lines)
    if send(message):
        atomic_json(path, updated)  # unsent alerts are retried by the next refresh
    return message
