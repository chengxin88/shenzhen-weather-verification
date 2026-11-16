"""Selected-station chart rows retain real pairs and immutable snapshot bounds."""
import gzip
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import station_api
from station_common import GRID_DATASETS, GRID_MODELS, THRESHOLDS


class StationSelection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.generation = '20261105T000000'
        self.directory = self.root/'snapshots'/self.generation
        self.directory.mkdir(parents=True)
        (self.root/'current.json').write_text(json.dumps(dict(generation=self.generation)))
        (self.directory/'meta.json').write_text(json.dumps(dict(stations=[dict(id='59493'), dict(id='59287'), dict(id='empty')])) )
        self.env = patch.dict(os.environ, STATIONS_CACHE_DIR=str(self.root))
        self.env.start()
        with sqlite3.connect(self.directory/'pairs.sqlite') as db:
            db.execute('CREATE TABLE pairs(model TEXT,run TEXT,lead INTEGER,valid TEXT,station TEXT,var TEXT,fc REAL,ob REAL)')
            for variable in GRID_DATASETS:
                with gzip.open(self.directory/f'grid-rows-{variable}.json.gz', 'wt') as f:
                    json.dump([], f)
                for model in ('ec01', 'gfs'):
                    for lead in (6, 24, 48):
                        for at in ('2026-11-03T15:00:00Z', '2026-11-03T16:00:00Z', '2026-11-04T15:00:00Z', '2026-11-04T16:00:00Z'):
                            for ident, fc, ob in [('59493', .2, 0.), ('59287', 6., 5.)]:
                                db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?)',
                                           (model, '2026-11-03T00:00:00Z', lead, at, ident, variable, fc, ob))
                for fc, ob in [(None, 1.), (float('inf'), 1.), (1., None), (1., float('inf'))]:
                    db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?)',
                               ('ec01', '2026-11-03T00:00:00Z', 24, '2026-11-04T00:00:00Z', '59493', variable, fc, ob))

    def tearDown(self):
        station_api.cached_json.cache_clear()
        self.env.stop()
        self.tmp.cleanup()

    def params(self, variable='rain6', **extra):
        return dict(dataset=GRID_DATASETS[variable], station='59493', start='2026-11-04',
                    end='2026-11-04', generation=self.generation, leads='6,24', **extra)

    def test_each_variable_reads_only_selected_station_and_real_bjt_times(self):
        before = {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in self.directory.iterdir()}
        for variable in GRID_DATASETS:
            params = self.params(variable)
            for ident, fc, ob in [('59493', .2, 0.), ('59287', 6., 5.)]:
                params['station'] = ident
                rows = station_api.dispatch('grid-rows', urlencode(params))
                grades=[r for r in rows if r.get('grade')]
                self.assertEqual(len(grades),48 if variable=='rain24' else 0)
                rows=[r for r in rows if not r.get('grade')]
                self.assertEqual(len(rows), 8*len(THRESHOLDS.get(variable, [None])))
                self.assertEqual({r['station'] for r in rows}, {ident})
                self.assertEqual({r['valid'] for r in rows}, {'2026-11-04T00:00:00', '2026-11-04T23:00:00'})
                self.assertEqual({r['run'] for r in rows}, {'2026-11-03T08:00:00'})
                self.assertEqual({r['lead'] for r in rows}, {6, 24})
                self.assertEqual({r['model'] for r in rows}, {GRID_MODELS['ec01'], GRID_MODELS['gfs']})
                for r in rows:
                    self.assertEqual(r['n'], 1)
                    if variable in THRESHOLDS:
                        yf, yo = fc >= r['threshold'], ob >= r['threshold']
                        self.assertEqual((r['H'], r['M'], r['F']), (int(yf and yo), int(not yf and yo), int(yf and not yo)))
                    else:
                        self.assertEqual((r['fc'], r['ob']), (fc, ob))
                        self.assertAlmostEqual(r['RMSE'], abs(fc-ob))
                        self.assertIsNone(r['CC'])
        self.assertEqual(before, {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in self.directory.iterdir()})

    def test_empty_station_never_returns_region_scores(self):
        params = self.params(); params['station'] = 'empty'
        self.assertEqual(station_api.dispatch('grid-rows', urlencode(params)), [])

    def test_bad_station_and_missing_dates_are_rejected(self):
        params = self.params(); params['station'] = "59493' OR 1=1 --"
        with self.assertRaises(ValueError): station_api.dispatch('grid-rows', urlencode(params))
        with self.assertRaises(ValueError): station_api.dispatch('grid-rows', urlencode(dict(dataset=GRID_DATASETS['t'], station='59493')))


if __name__ == '__main__':
    unittest.main()
