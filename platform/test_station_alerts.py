"""OGIMET network-failure retry and WeCom outage alerts."""
from datetime import datetime, timedelta
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import requests
from station_common import UTC, atomic_json, iso
import station_alerts
import station_sources

GOOD = '59287,2026,10,25,09,00,AAXX 25091 59287 14/80 /0502 10280 20184 30048 40131 52003 60001 333 10297 20196 3/019 58055 70000 90761 91108=='


def response(body):
    result = MagicMock(); result.__enter__.return_value = result
    result.iter_content.return_value = [body.encode()]
    return result


class OgimetNetworkRetry(unittest.TestCase):
    def run_once(self, root, client, at):
        start = at - timedelta(hours=30)
        with patch.object(station_sources, 'now', return_value=at), \
             patch.object(station_sources.time, 'sleep'):
            station_sources.ogimet(client, root, start, at)
        return json.loads((root / 'ogimet-journal.json').read_text())

    def test_timeouts_pause_then_latch_after_limit(self):
        at = datetime(2026, 11, 2, 0, 40, tzinfo=UTC)
        client = MagicMock(); client.get.side_effect = requests.ReadTimeout('read timed out')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = self.run_once(root, client, at)
            self.assertIsNone(journal.get('stopped'))
            self.assertEqual(journal['transient_failures'], 1)
            self.assertEqual(journal['retry_after'], iso(at + station_sources.TRANSIENT_RETRY))
            self.assertEqual(client.get.call_count, 1)  # first request fails, the rest of the run is skipped
            self.run_once(root, client, at + timedelta(hours=1))  # inside the pause: no request
            self.assertEqual(client.get.call_count, 1)
            journal = self.run_once(root, client, at + timedelta(hours=3))
            self.assertEqual(journal['transient_failures'], 2); self.assertIsNone(journal.get('stopped'))
            journal = self.run_once(root, client, at + timedelta(hours=6))
            self.assertIn('3 consecutive network failures', journal['stopped'])
            self.run_once(root, client, at + timedelta(hours=12))
            self.assertEqual(client.get.call_count, 3)
            self.assertTrue(all(r['state'] == 'error' and r['transient'] for r in journal['requests']))

    def test_success_resets_failure_count(self):
        at = datetime(2026, 11, 2, 0, 40, tzinfo=UTC)
        client = MagicMock(); client.get.side_effect = [requests.ConnectionError('reset')] + [response(GOOD)] * 8
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.run_once(root, client, at)
            journal = self.run_once(root, client, at + timedelta(hours=3))
            self.assertEqual(journal['transient_failures'], 0); self.assertIsNone(journal['retry_after'])
            self.assertIsNone(journal.get('stopped'))

    def test_http_refusal_still_latches_immediately(self):
        at = datetime(2026, 11, 2, 0, 40, tzinfo=UTC)
        refused = response('<html>Too many requests</html>')
        client = MagicMock(); client.get.side_effect = [refused]
        with tempfile.TemporaryDirectory() as temporary:
            journal = self.run_once(Path(temporary), client, at)
            self.assertIsNotNone(journal['stopped']); self.assertFalse(journal.get('transient_failures'))


class OutageAlerts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.sent = []
        patcher = patch.object(station_alerts, 'send', side_effect=lambda text: self.sent.append(text) or True)
        patcher.start(); self.addCleanup(patcher.stop); self.addCleanup(self.temporary.cleanup)
        self.at = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)

    def status(self, latest, stopped=None):
        atomic_json(self.root / 'obs-status.json', {'sources': {'ogimet': {'latest': latest}}})
        atomic_json(self.root / 'ogimet-journal.json', {'requests': [], 'stopped': stopped})

    def test_healthy_sends_nothing(self):
        self.status(iso(self.at - timedelta(hours=3)))
        self.assertIsNone(station_alerts.check(self.root, self.at)); self.assertEqual(self.sent, [])

    def test_latch_and_stale_alert_once_then_repeat_daily_then_recover(self):
        self.status('2026-10-30T15:00:00Z', 'timeout')
        message = station_alerts.check(self.root, self.at)
        self.assertIn('OGIMET 已锁住', message); self.assertIn('58 小时未更新', message)
        self.assertIsNone(station_alerts.check(self.root, self.at + timedelta(hours=3)))
        self.assertEqual(len(self.sent), 1)
        self.assertIsNotNone(station_alerts.check(self.root, self.at + timedelta(hours=24)))
        self.status(iso(self.at + timedelta(hours=26)))
        message = station_alerts.check(self.root, self.at + timedelta(hours=27))
        self.assertIn('已恢复：OGIMET 停止标记', message); self.assertIn('已恢复：OGIMET 实况断档', message)
        self.assertIsNone(station_alerts.check(self.root, self.at + timedelta(hours=30)))
        self.assertEqual(len(self.sent), 3)

    def test_new_latch_reason_alerts_again(self):
        self.status(iso(self.at), 'first'); station_alerts.check(self.root, self.at)
        self.status(iso(self.at), 'second')
        self.assertIn('second', station_alerts.check(self.root, self.at + timedelta(hours=1)))

    def test_failed_send_is_retried_next_refresh(self):
        self.status(iso(self.at), 'latched')
        with patch.object(station_alerts, 'send', return_value=False):
            station_alerts.check(self.root, self.at)
        self.assertFalse((self.root / 'alert-state.json').exists())
        self.assertIsNotNone(station_alerts.check(self.root, self.at + timedelta(hours=3)))


if __name__ == '__main__':
    unittest.main()
