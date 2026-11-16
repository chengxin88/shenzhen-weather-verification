"""Typhoon archive reads must refresh lists and active storms, never finished ones."""
import json, os, tempfile, time, unittest
from pathlib import Path

import typhoon_api as T


class Refresh(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); T._attempted.clear(); T._refreshing.clear()
        self.addCleanup(T._attempted.clear)

    def write(self, name, payload, age):
        path = self.root/name; path.write_text(json.dumps(payload), encoding='utf-8')
        stamp = time.time()-age; os.utime(path, (stamp, stamp)); return path

    def test_rules(self):
        old = T.REFRESH_SECONDS+1
        self.assertTrue(T.needs_refresh('catalog_2026.json', {}, old, 2026))
        self.assertFalse(T.needs_refresh('catalog_2026.json', {}, 60, 2026))
        self.assertFalse(T.needs_refresh('catalog_2020.json', {}, old, 2026))
        start = {'typhoon': [0]*7+['start']}; stop = {'typhoon': [0]*7+['stop']}
        self.assertTrue(T.needs_refresh('storm_1.json', start, old, 2026))
        self.assertFalse(T.needs_refresh('storm_1.json', stop, old, 2026))
        self.assertTrue(T.needs_refresh('agency_202631.json', {'isactive': '1'}, old, 2026))
        self.assertFalse(T.needs_refresh('agency_202631.json', {'isactive': '0'}, old, 2026))
        self.assertFalse(T.needs_refresh('jma_targetTc.json', {}, old, 2026))

    def test_stale_catalog_is_replaced(self):
        self.write('catalog_2026.json', {'typhoonList': [1]}, 7200)
        read = T.cached_reader(lambda n: self.root/n, lambda url: {'typhoonList': [1, 2]})
        payload, _ = read('u', 'catalog_2026.json')
        self.assertEqual(payload['typhoonList'], [1, 2])
        self.assertEqual(json.loads((self.root/'catalog_2026.json').read_text())['typhoonList'], [1, 2])

    def test_failure_keeps_archive_and_backs_off(self):
        self.write('catalog_2026.json', {'typhoonList': [1]}, 7200); calls = []
        def fail(url):
            calls.append(url); raise OSError('offline')
        read = T.cached_reader(lambda n: self.root/n, fail)
        self.assertEqual(read('u', 'catalog_2026.json')[0]['typhoonList'], [1])
        self.assertEqual(read('u', 'catalog_2026.json')[0]['typhoonList'], [1])
        self.assertEqual(len(calls), 1)

    def test_bad_payload_does_not_overwrite(self):
        self.write('storm_9.json', {'typhoon': [0]*7+['start']}, 7200)
        read = T.cached_reader(lambda n: self.root/n, lambda url: {'error': 'busy'})
        self.assertIn('typhoon', read('u', 'storm_9.json')[0])

    def test_finished_storm_stays_offline(self):
        self.write('storm_9.json', {'typhoon': [0]*7+['stop']}, 7200*24)
        def never(url): raise AssertionError('finished storm must not refresh')
        self.assertEqual(T.cached_reader(lambda n: self.root/n, never)('u', 'storm_9.json')[0]['typhoon'][7], 'stop')


if __name__ == '__main__': unittest.main()
