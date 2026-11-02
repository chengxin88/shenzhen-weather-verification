"""Synthetic spatial rows, additive BJT-day map stats, and read-only HTTP products."""
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch
import gzip
import json
import math
import os
import sqlite3
import tempfile
import threading
import unittest
import numpy as np
from station_common import (GRID_DATASETS, GRID_MODELS, THRESHOLDS, UTC, atomic_json, bjt, iso)
from station_score import database, grid_aggregates
import station_api
import server


class GridProducts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.directory = self.root/'snapshots/20261026T000000'
        self.directory.mkdir(parents=True)
        self.env = patch.dict(os.environ, {'STATIONS_CACHE_DIR': str(self.root)})
        self.env.start()
        self.db = database(self.directory/'pairs.sqlite')
        self.pairs = []
        # UTC 15/18 cross the BJT day boundary; endpoints must use BJT dates.
        for at in ['2026-10-23T15:00:00Z', '2026-10-23T18:00:00Z',
                   '2026-10-24T15:00:00Z', '2026-10-24T18:00:00Z']:
            for lead in (0, 24, 25, 72, 73, 168):
                for model in GRID_MODELS:
                    for variable in GRID_DATASETS:
                        for i, (fc, ob) in enumerate(zip([0, .1, 5, 20], [.1, 0, 10, 20])):
                            fc += lead/100 if variable == 't' else 0
                            run = iso(datetime.fromisoformat(at.replace('Z', '+00:00'))-timedelta(hours=lead))
                            self.pairs.append((model, run, lead, at, str(59000+i), variable,
                                               fc, ob, 'ogimet', 0, at))
        self.db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)', self.pairs)
        self.db.commit()
        atomic_json(self.root/'current.json', {'generation': self.directory.name})
        atomic_json(self.directory/'meta.json', dict(stations=[dict(id=str(59000+i)) for i in range(4)],
                    generated='2026-10-26T00:00:00Z', window_end='2026-10-26T00:00:00Z'))
        grid_aggregates(self.db, self.directory, datetime(2026, 10, 26, tzinfo=UTC))

    def tearDown(self):
        self.db.close()
        station_api.cached_json.cache_clear()
        self.env.stop()
        self.tmp.cleanup()

    def rows(self, variable):
        with gzip.open(self.directory/f'grid-rows-{variable}.json.gz', 'rt') as handle:
            return json.load(handle)

    def assert_maths(self, actual, pairs):
        fc, ob = np.array([(r[6], r[7]) for r in pairs]).T
        self.assertEqual(actual['n'], len(pairs))
        for key, expected in dict(ME=np.mean(fc-ob), MAE=np.mean(abs(fc-ob)),
                                  RMSE=np.sqrt(np.mean((fc-ob)**2)), CC=np.corrcoef(fc,ob)[0,1] if len(fc)>=3 and np.std(fc)>1e-10 and np.std(ob)>1e-10 else None).items():
            if key == 'CC' and (len(fc)<3 or np.std(fc)<1e-10 or np.std(ob)<1e-10):
                self.assertIsNone(actual[key])
            else: self.assertAlmostEqual(actual[key], expected, places=9, msg=key)

    def test_spatial_rows_metadata_model_mapping_and_bjt(self):
        rows = self.rows('t')
        self.assertEqual({r['model'] for r in rows}, set(GRID_MODELS.values()))
        for row in rows:
            pairs = [p for p in self.pairs if GRID_MODELS[p[0]] == row['model'] and p[2] == row['lead']
                     and bjt(p[3]) == row['valid'] and p[5] == 't']
            self.assert_maths(row, pairs)
            self.assertEqual(row['run'], bjt(pairs[0][1]))
            self.assertNotIn('Z', row['valid'])
        datasets = station_api.dispatch('grid-datasets', '')
        self.assertEqual({d['id'] for d in datasets}, set(GRID_DATASETS.values()))
        for dataset in datasets:
            self.assertEqual(dataset['source'], 'station')
            self.assertEqual(dataset['reference'], '华南站点实况')
            self.assertTrue(dataset['has_n'])
            self.assertEqual(dataset['start'], '2026-10-23T23:00:00')
            self.assertEqual(dataset['end'], '2026-10-25T02:00:00')
            self.assertIn('IEM METAR', dataset['source_note'])
        for variable in ('rain6', 'rain24'):
            for row in self.rows(variable):
                fc, ob = np.array([(p[6],p[7]) for p in self.pairs if GRID_MODELS[p[0]]==row['model']
                    and p[2]==row['lead'] and bjt(p[3])==row['valid'] and p[5]==variable]).T
                yesf, yeso = fc>=row['threshold'], ob>=row['threshold']
                self.assertEqual((row['n'], row['H'], row['M'], row['F']),
                    (len(fc), sum(yesf & yeso), sum(~yesf & yeso), sum(yesf & ~yeso)))

    def test_map_subsets_match_direct_pair_scores_and_do_not_write(self):
        before = {p.name:(p.stat().st_mtime_ns, p.stat().st_size) for p in self.directory.iterdir()}
        for start, end, leads in [('2026-10-24','2026-10-24',[24,25,72]),
                                  ('2026-10-23','2026-10-25',[0,73,168])]:
            for variable in GRID_DATASETS:
                for threshold in THRESHOLDS.get(variable, [None]):
                    params = dict(model=GRID_MODELS['ec01'], var=variable, start=start, end=end, leads=','.join(map(str,leads)))
                    if threshold is not None: params['threshold'] = threshold
                    rows = station_api.dispatch('map', urlencode(params))['rows']
                    self.assertEqual(len(rows),4)
                    for row in rows:
                        pairs = [p for p in self.pairs if p[0]=='ec01' and p[4]==row['station'] and p[5]==variable
                                 and p[2] in leads and start<=bjt(p[3])[:10]<=end]
                        if variable == 't': self.assert_maths(row, pairs)
                        else:
                            fc, ob = np.array([(p[6],p[7]) for p in pairs]).T
                            yf, yo = fc>=threshold, ob>=threshold
                            self.assertEqual((row['n'],row['H'],row['M'],row['F']),
                                             (len(fc), sum(yf&yo), sum(~yf&yo), sum(yf&~yo)))
                            self.assertEqual(row['TS'], None if not sum(yf|yo) else sum(yf&yo)/sum(yf|yo))
        self.assertEqual(before, {p.name:(p.stat().st_mtime_ns, p.stat().st_size) for p in self.directory.iterdir()})

    def test_custom_series_bjt_bounds_and_invalid_queries(self):
        result = station_api.dispatch('series', urlencode(dict(station='59000',model=GRID_MODELS['ec01'],
                    var='t',lead=24,start='2026-10-24',end='2026-10-24')))
        self.assertEqual([r['valid'] for r in result['series']], ['2026-10-23T18:00:00Z','2026-10-24T15:00:00Z'])
        for query in ['start=2026-02-30&end=2026-10-24', 'start=2026-10-25&end=2026-10-24',
                      'start=2026-10-24&end=2026-10-24&lead=169',
                      'start=2026-10-24&end=2026-10-24&lead=24&leads=24,48',
                      'start=2026-10-24&end=2026-10-24&var=rain6&threshold=2',
                      'start=2026-10-24&end=2026-10-24&models=ec01',
                      'start=2026-10-24&start=2026-10-25&end=2026-10-26']:
            with self.assertRaises(ValueError): station_api.dispatch('map',query)
        with self.assertRaises(ValueError): station_api.dispatch('grid-rows','dataset=../../pairs')

    def test_run_hour_filters_pairs_before_map_scoring_and_series_limits(self):
        self.db.execute('DELETE FROM pairs')
        pairs = []
        # Include a previous-UTC-day 18Z run (02 BJT), and the next BJT day's boundary.
        for cycle, run in enumerate(['2026-10-22T18:00:00Z', '2026-10-23T00:00:00Z',
                                     '2026-10-23T06:00:00Z', '2026-10-23T12:00:00Z',
                                     '2026-10-23T18:00:00Z']):
            for lead in (24, 48):
                valid = iso(datetime.fromisoformat(run.replace('Z','+00:00'))+timedelta(hours=lead))
                for variable in GRID_DATASETS:
                    for i, (fc, ob) in enumerate(zip([0, .1, 5, 20], [.1, 0, 10, 20])):
                        pairs.append(('ec01',run,lead,valid,str(59000+i),variable,
                                      fc+cycle,ob,'ogimet',0,valid))
        self.db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',pairs)
        for variable in GRID_DATASETS:
            for i, (fc, ob) in enumerate([(None,1),(float('inf'),1),(1e308,1),(1,None),(1,float('inf'))]):
                self.db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                ('ec01','2026-10-23T00:00:00Z',24,'2026-10-24T00:00:00Z',
                                 f'invalid{i}',variable,fc,ob,'ogimet',0,'2026-10-24T00:00:00Z'))
        self.db.commit()
        grid_aggregates(self.db,self.directory,datetime(2026,10,26,tzinfo=UTC))
        before = {p.name:(p.stat().st_mtime_ns,p.stat().st_size) for p in self.directory.iterdir()}
        for hour in ('all','02','08','14','20'):
            for variable in GRID_DATASETS:
                params = dict(model='ec01',var=variable,leads='24,48',start='2026-10-24',end='2026-10-24',hour=hour)
                threshold = THRESHOLDS.get(variable,[None])[-1]
                if threshold is not None: params['threshold'] = threshold
                result = station_api.dispatch('map',urlencode(params))
                expected = [p for p in pairs if p[5]==variable and bjt(p[3])[:10]=='2026-10-24'
                            and (hour=='all' or bjt(p[1])[11:13]==hour)]
                self.assertEqual(result['hour'],hour)
                self.assertEqual(sum(r['n'] for r in result['rows']),len(expected))
                for row in result['rows']:
                    selected = [p for p in expected if p[4]==row['station']]
                    if variable=='t': self.assert_maths(row,selected)
                    else:
                        fc,ob = np.array([(p[6],p[7]) for p in selected]).T
                        yf,yo = fc>=threshold,ob>=threshold
                        self.assertEqual((row['n'],row['H'],row['M'],row['F']),
                                         (len(fc),sum(yf&yo),sum(~yf&yo),sum(yf&~yo)))
                params.update(station='59000')
                one = station_api.dispatch('map',urlencode(params))
                self.assertEqual([r['station'] for r in one['rows']],['59000'])
            result = station_api.dispatch('series',urlencode(dict(station='59000',model='ec01',var='t',lead=24,
                        start='2026-10-24',end='2026-10-24',hour=hour)))
            expected = [p for p in pairs if p[4]=='59000' and p[5]=='t' and p[2]==24
                        and bjt(p[3])[:10]=='2026-10-24' and (hour=='all' or bjt(p[1])[11:13]==hour)]
            self.assertEqual([r['run'] for r in result['series']],[p[1] for p in expected])
        for operation in ('map','series','station'):
            for hour in ('00','8','24','08,20',"08' OR 1=1"):
                with self.assertRaises(ValueError):
                    station_api.dispatch(operation,urlencode(dict(start='2026-10-24',end='2026-10-24',hour=hour)))
        self.assertEqual(before,{p.name:(p.stat().st_mtime_ns,p.stat().st_size) for p in self.directory.iterdir()})

    def test_http_streaming_gzip_identity_and_read_only_map_series(self):
        import http.client
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
        before = {p.name:(p.stat().st_mtime_ns,p.stat().st_size) for p in self.directory.iterdir()}
        try:
            for encoding in ('gzip', 'identity', 'gzip;q=0'):
                connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
                connection.request('GET', '/Verification/test/api/stations/grid-rows?'+urlencode({'dataset':GRID_DATASETS['t']}),
                                   headers={'Accept-Encoding': encoding})
                response = connection.getresponse(); payload = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Vary'), 'Accept-Encoding')
                self.assertEqual(response.getheader('Content-Encoding'), 'gzip' if encoding=='gzip' else None)
                self.assertEqual(json.loads(gzip.decompress(payload) if encoding=='gzip' else payload), self.rows('t'))
                connection.close()
            for operation, params in [('map',dict(var='rain24',model='ec01',threshold=10,lead=24,start='2026-10-24',end='2026-10-24')),
                                      ('series',dict(station='59000',var='t',model='ec01',lead=24,start='2026-10-24',end='2026-10-24'))]:
                connection = http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
                connection.request('GET','/Verification/test/api/stations/'+operation+'?'+urlencode(params))
                response=connection.getresponse();self.assertEqual(response.status,200)
                self.assertNotIn(b'NaN',response.read());connection.close()
        finally:
            httpd.shutdown();httpd.server_close();worker.join()
        self.assertEqual(before,{p.name:(p.stat().st_mtime_ns,p.stat().st_size) for p in self.directory.iterdir()})

    def test_undefined_cc_for_two_stations_and_constant_spatial_fields(self):
        self.db.execute('DELETE FROM pairs')
        samples = [('ec01','2026-10-23T00:00:00Z',24,'2026-10-24T00:00:00Z',str(i),'t',fc,ob,'ogimet',0,'2026-10-24T00:00:00Z')
                   for i,(fc,ob) in enumerate([(1,2),(3,4)])]
        self.db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)', samples)
        grid_aggregates(self.db,self.directory,datetime(2026,10,26,tzinfo=UTC))
        self.assertIsNone(self.rows('t')[0]['CC'])
        self.db.execute('UPDATE pairs SET fc=1')
        self.db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',(*samples[0][:4],'third','t',1,7,*samples[0][8:]))
        grid_aggregates(self.db,self.directory,datetime(2026,10,26,tzinfo=UTC))
        self.assertIsNone(self.rows('t')[0]['CC'])


if __name__ == '__main__': unittest.main()
