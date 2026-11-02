"""Offline-friendly source downloads. All network traffic uses explicit 17908.

OGIMET requests are journalled *before* sending, never retried automatically,
and separated by at least 20 seconds across invocations. An error response
(HTTP status, HTML, rate limit, malformed data) persists a stop latch requiring
operator investigation. A plain network failure (timeout, refused or dropped
connection) only pauses OGIMET until a later refresh; after TRANSIENT_LIMIT
consecutive network failures it latches as well.
"""
from datetime import timedelta
from pathlib import Path
import csv
import io
import re
import time
import xml.etree.ElementTree as ET
import requests
from station_common import PROXY, atomic_json, digest, iso, now, read_json, stamp

S3 = 'https://noaa-ghcnh-pds.s3.amazonaws.com/'
# The timer runs every three hours: a 2.5 h pause skips no scheduled refresh.
TRANSIENT_RETRY = timedelta(hours=2, minutes=30)
TRANSIENT_LIMIT = 3


def session():
    result = requests.Session()
    result.trust_env = False
    result.proxies = {'http': PROXY, 'https': PROXY}
    result.headers['User-Agent'] = 'StationVerification/1.0 (single-user research; cached requests)'
    return result


def cached_download(client, url, path, daily=False):
    """Conditional GET, streamed to disk, with daily freshness and atomic writes."""
    meta_path = path.with_name(path.name + '.http.json')
    meta = read_json(meta_path, {})
    if path.is_file() and daily and meta.get('checked', '')[:10] == iso(now())[:10]:
        return False
    headers = {'If-None-Match': meta['etag']} if path.is_file() and meta.get('etag') else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    with client.get(url, headers=headers, timeout=(15, 120), stream=True) as response:
        if response.status_code == 304:
            meta['checked'] = iso(now()); atomic_json(meta_path, meta)
            return False
        response.raise_for_status()
        temporary = path.with_name(path.name + '.next')
        length = 0
        try:
            with temporary.open('wb') as handle:
                for block in response.iter_content(256 * 1024):
                    length += len(block)
                    if length > 180 * 1024 * 1024:
                        raise ValueError('Source file exceeds 180 MiB limit')
                    handle.write(block)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        atomic_json(meta_path, dict(etag=response.headers.get('ETag'), checked=iso(now()), bytes=length))
    return True


def metadata(client, raw):
    for name, url in [('ghcnh-station-list.txt', S3 + 'hourly/doc/ghcnh-station-list.txt'),
                      ('CN__ASOS.geojson', 'https://mesonet.agron.iastate.edu/geojson/network/CN__ASOS.geojson'),
                      ('ghcnh-inventory.xml', S3 + '?list-type=2&prefix=hourly/access/by-year/2026/psv/GHCNh_CHM&max-keys=1000')]:
        if not (raw / name).is_file():
            cached_download(client, url, raw / name)


def ogimet(client, root, start, end, backfill=False):
    journal_path = root / 'ogimet-journal.json'
    journal = read_json(journal_path, {'requests': [], 'stopped': None})
    if journal.get('stopped'):
        print('OGIMET stopped:', journal['stopped'], flush=True)
        return
    if journal.get('retry_after') and now() < stamp(journal['retry_after']):
        print('OGIMET network pause until', journal['retry_after'], flush=True)
        return
    raw = root / 'raw/ogimet'; raw.mkdir(parents=True, exist_ok=True)
    # Three-day non-overlapping UTC windows: 30 requests for a 30-day backfill.
    windows = []
    cursor = start.replace(second=0, microsecond=0)
    while cursor < end:
        stop = min(cursor + timedelta(days=3), end.replace(second=0, microsecond=0))
        windows.append((cursor, stop)); cursor = stop
    for begin, stop in windows:
        # The expanded western edge also intersects WMO block 56 (Yunnan).
        for block in (56, 57, 58, 59):
            params = dict(block=str(block), begin=begin.strftime('%Y%m%d%H%M'),
                          end=(stop - timedelta(minutes=1)).strftime('%Y%m%d%H%M'))
            key = f"{block}_{params['begin']}_{params['end']}"
            if any(r['key'] == key for r in journal['requests']):
                continue
            total_backfill = sum(bool(r.get('backfill')) for r in journal['requests'])
            if backfill and total_backfill >= 120:
                journal['stopped'] = 'Backfill request budget (120) reached'
                atomic_json(journal_path, journal); return
            if journal['requests']:
                wait = 20.5 - (time.time() - journal['requests'][-1]['sent_epoch'])
                if wait > 0:
                    time.sleep(wait)
            entry = dict(key=key, sent_epoch=time.time(), sent=iso(now()), backfill=backfill,
                         block=block, begin=params['begin'], end=params['end'], state='sending')
            journal['requests'].append(entry); atomic_json(journal_path, journal)
            print('OGIMET', key, flush=True)
            try:
                with client.get('https://www.ogimet.com/cgi-bin/getsynop', params=params,
                                timeout=(15, 90), stream=True) as response:
                    response.raise_for_status()
                    chunks = []; size = 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 12 * 1024 * 1024:
                            raise ValueError('OGIMET response exceeded 12 MiB')
                        chunks.append(chunk)
                    content = b''.join(chunks).decode('utf-8', errors='replace')
                lines = content.splitlines()
                records = [line for line in lines if re.match(r'^\d{5},\d{4},', line)]
                bad = [line for line in lines if line.strip() and not line.startswith('#') and line not in records]
                if not records or bad or re.search(r'<html|<!doctype|rate.limit|too many|exceeded|access.denied', content, re.I):
                    # Preserve the response for manual review, but never ingest it.
                    (raw / (key + '.error')).write_text(content)
                    raise ValueError('Non-data/error response: ' + content[:240].replace('\n', ' '))
                path = raw / (key + '.txt'); temporary = path.with_suffix('.next')
                temporary.write_text(content); temporary.replace(path)
                entry.update(state='ok', lines=len(records), bytes=size)
                journal['transient_failures'] = 0; journal['retry_after'] = None
            except (requests.Timeout, requests.ConnectionError) as exc:
                # No response reached us, so this is not a refusal by OGIMET.
                failures = int(journal.get('transient_failures') or 0) + 1
                entry.update(state='error', transient=True, reason=str(exc)[:400])
                journal['transient_failures'] = failures
                if failures >= TRANSIENT_LIMIT:
                    journal['retry_after'] = None
                    journal['stopped'] = (f"{iso(now())}: {failures} consecutive network failures; "
                                          f"last: {entry['reason']}")
                    print('OGIMET stopped:', journal['stopped'], flush=True)
                else:
                    journal['retry_after'] = iso(now() + TRANSIENT_RETRY)
                    print(f'OGIMET network failure {failures}/{TRANSIENT_LIMIT}; retry after',
                          journal['retry_after'], flush=True)
                atomic_json(journal_path, journal)
                return
            except (requests.RequestException, ValueError) as exc:
                entry.update(state='error', reason=str(exc)[:400])
                journal['stopped'] = f"{iso(now())}: {entry['reason']}"
                atomic_json(journal_path, journal); print('OGIMET stopped:', journal['stopped'], flush=True)
                return
            atomic_json(journal_path, journal)


def ghcnh(client, raw, stations):
    errors = []
    for station in stations:
        if 'ghcnh' not in station['sources']:
            continue
        ident = station['ghcnh_id']
        path = raw / 'ghcnh' / (ident + '_2026.psv')
        try:
            changed = cached_download(client, S3 + f'hourly/access/by-year/2026/psv/GHCNh_{ident}_2026.psv', path, daily=True)
            print('GHCNh', ident, 'updated' if changed else 'cached', flush=True)
        except (requests.RequestException, ValueError) as exc:
            errors.append({'source': 'ghcnh', 'station': ident, 'error': str(exc)[:250]})
    return errors


def iem(client, root, stations, start, end):
    ids = [s['id'] for s in stations if s['type'] == 'metar']
    if not ids:
        return []
    # One batch per refresh; at most three ten-day batches for initial backfill.
    raw = root / 'raw/iem'; raw.mkdir(parents=True, exist_ok=True)
    errors = []
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(days=10), end)
        key = cursor.strftime('%Y%m%d%H%M') + '_' + stop.strftime('%Y%m%d%H%M') + '_' + digest(sorted(ids))[:12]
        path = raw / (key + '.csv')
        if not path.is_file():
            params = [('station', ident) for ident in ids]
            params += [('data', field) for field in ('tmpf', 'dwpf', 'sknt', 'drct', 'mslp')]
            params += list(dict(tz='Etc/UTC', format='onlycomma', latlon='no', missing='M', trace='T',
                                sts=cursor.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                ets=stop.strftime('%Y-%m-%dT%H:%M:%SZ'), report_type='3').items())
            try:
                response = client.get('https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py', params=params, timeout=(15, 180))
                response.raise_for_status()
                if not response.text.startswith('station,valid,'):
                    raise ValueError('IEM response is not the requested CSV')
                path.write_text(response.text)
                print('IEM', key, len(response.content), flush=True)
            except (requests.RequestException, ValueError) as exc:
                errors.append({'source': 'iem', 'error': str(exc)[:250]})
        cursor = stop
    return errors
