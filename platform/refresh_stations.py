#!/usr/bin/env python3
"""One-shot station refresh. HTTP routes never import or invoke this module."""
import argparse
import fcntl
import os
import resource
import sys
import time
from datetime import timedelta
from station_common import ROOT, atomic_json, cache_root, iso, now, read_json, stamp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backfill', type=int, metavar='DAYS')
    parser.add_argument('--fetch-only', action='store_true')
    parser.add_argument('--score-only', action='store_true')
    parser.add_argument('--end', type=stamp, help='Fixed UTC scoring end (requires --score-only)')
    args = parser.parse_args()
    if args.backfill is not None and not 1 <= args.backfill <= 31:
        parser.error('--backfill must be between 1 and 31')
    if args.fetch_only and args.score_only:
        parser.error('choose either --fetch-only or --score-only')
    if args.end is not None and not args.score_only:
        parser.error('--end requires --score-only')
    root = cache_root(); root.mkdir(parents=True, exist_ok=True)
    with (root / 'refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another station refresh owns the flock; skipped.'); return 0
        started = time.monotonic(); end = args.end or now().replace(second=0, microsecond=0)
        status = dict(started=iso(end), state='running', backfill_days=args.backfill, errors=[])
        atomic_json(root / 'refresh-status.json', status)
        try:
            if not args.score_only:
                fetch_started = time.monotonic()
                from station_sources import session, metadata, ogimet, ghcnh, iem
                from build_stations import build
                client = session(); metadata(client, root / 'raw')
                start = end - (timedelta(days=args.backfill) if args.backfill else timedelta(hours=30))
                ogimet(client, root, start, end, backfill=bool(args.backfill))
                journal = read_json(root / 'ogimet-journal.json', {})
                if journal.get('stopped'):
                    status['errors'].append({'source':'ogimet','error':journal['stopped']})
                elif journal.get('retry_after'):
                    status['errors'].append({'source':'ogimet','error':
                        f"network failure {journal.get('transient_failures')}; retry after {journal['retry_after']}"})
                # Freeze a version in the cache. Updating the reviewed repo table
                # remains an explicit builder operation, not a timer side effect.
                table = build(output=root / 'stations.json')
                status['errors'] += iem(client, root, table['stations'],
                                        end - timedelta(days=args.backfill) if args.backfill else end - timedelta(hours=26), end)
                status['errors'] += ghcnh(client, root / 'raw', table['stations'])
                status['fetch_seconds'] = round(time.monotonic()-fetch_started,3)
                print('TIMING fetch',status['fetch_seconds'],'seconds',flush=True)
            else:
                status['fetch_seconds'] = 0
                print('TIMING fetch 0 seconds (--score-only)',flush=True)
            if not args.fetch_only:
                from station_score import refresh
                status['scoring'] = refresh(root, end)
            status['state'] = 'complete' if not status['errors'] else 'partial'
        except Exception as exc:
            status.update(state='error', error=f'{type(exc).__name__}: {exc}')
            raise
        except KeyboardInterrupt:
            status.update(state='interrupted')
            raise
        finally:
            status.update(finished=iso(now()), elapsed_seconds=round(time.monotonic() - started, 2),
                          peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1))
            atomic_json(root / 'refresh-status.json', status)
            print(status, flush=True)
            if os.environ.get('STATIONS_ALERTS') == '1':
                try:
                    from station_alerts import check
                    check(root)
                except Exception as exc:  # an alert failure must never fail the refresh
                    print('Station alert check failed:', exc, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
