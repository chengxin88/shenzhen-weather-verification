"""Xinjiang stations are scored, but never enter the southern pools."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import build_stations
import station_score
from station_common import GRID_MODELS, MODELS, XINJIANG, XJ_BOUNDS, in_box


class Xinjiang(unittest.TestCase):
    def test_stations_lie_in_their_view_and_outside_the_southern_box(self):
        for item in XINJIANG.values():
            self.assertTrue(XJ_BOUNDS[0] <= item['lon'] <= XJ_BOUNDS[2] and XJ_BOUNDS[1] <= item['lat'] <= XJ_BOUNDS[3])
            self.assertFalse(in_box(item['lon'], item['lat']))

    def test_every_model_keeps_a_display_name(self):
        self.assertEqual(set(MODELS), set(GRID_MODELS))

    def test_wn3_jobs_own_disjoint_stations_of_one_model(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE pairs(model TEXT,station TEXT)')
        db.executemany('INSERT INTO pairs VALUES (?,?)', [('wn3', '52112'), ('wn3', '59493'), ('ecmwf', '52112')])
        for job, kept in (('wn3xj', '59493'), ('wn3', '52112')):
            model, scope = station_score.job_scope(job)
            self.assertEqual(model, 'wn3')
            found = db.execute('SELECT station FROM pairs WHERE model=?' + scope, (model,)).fetchall()
            self.assertEqual(len(found), 1)
            self.assertNotEqual(found[0][0], kept)
        self.assertEqual(station_score.job_scope('ecmwf'), ('ecmwf', ''))

    def test_southern_filter_excludes_only_xinjiang(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE pairs(station TEXT)')
        db.executemany('INSERT INTO pairs VALUES (?)', [('52112',), ('59493',), ('ZGGG',)])
        rows = db.execute(f'SELECT station FROM pairs WHERE {station_score.SOUTH_ONLY}').fetchall()
        self.assertEqual({r[0] for r in rows}, {'59493', 'ZGGG'})


class WN3Speed(unittest.TestCase):
    def test_download_and_scoring_agree_on_the_speed_field(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'gods-eye-weather/weather_backend'))
        import native_archive
        import weathernext3_native_cache as cache
        self.assertEqual(native_archive.WN3_FIELDS['surface_ws'], cache.VARIABLES['surface_ws'])
        self.assertNotIn('surface_ws', cache.CORE_KEYS, 'older runs must stay complete without it')


if __name__ == '__main__':
    unittest.main()
