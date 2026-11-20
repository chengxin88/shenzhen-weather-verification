"""Overview reads synthetic immutable snapshots; no producer/network dependencies."""
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import json
import os
import sqlite3
import tempfile
import time
import unittest

import guifan_scores as guifan
import station_api as api
from station_common import iso, stamp


class Overview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, STATIONS_CACHE_DIR=str(self.root), VERIFICATION_READ_ONLY='1')
        self.env.start()
        self.directory = self.make_snapshot('20261109T000000')

    def tearDown(self):
        api.overview_cached.cache_clear()
        api.cached_json.cache_clear()
        self.env.stop()
        self.tmp.cleanup()

    def make_snapshot(self, generation, extreme=False):
        directory = self.root/'snapshots'/generation
        directory.mkdir(parents=True)
        end = '2026-11-09T00:00:00Z'
        (directory/'meta.json').write_text(json.dumps(dict(window_end=end, generated=end,
            stations=[dict(id=s) for s in ('A','B','52112','52203','51495')])) )
        db = sqlite3.connect(directory/'pairs.sqlite')
        db.execute('CREATE TABLE pairs(model,run,lead,valid,station,var,fc,ob,trace)')
        pairs = []
        def add(model, lead, age, station, var, fc, ob=0, trace=0):
            valid = iso(stamp(end)-timedelta(days=age))
            pairs.append((model,iso(stamp(valid)-timedelta(hours=lead)),lead,valid,station,var,fc,ob,trace))
        for model in ('ec01','gfs'):
            add(model,0,1,'A','t',100)  # day 1 excludes lead zero
            add(model,3,1,'A','t',1)
            for age in (1,2,3): add(model,24,age,'B','t',3)
            add(model,25,1,'A','t',2)
            add(model,48,1,'B','t',4)
            add(model,49,1,'A','t',.5)
            add(model,72,1,'B','t',1.5)
            add(model,24,8,'A','t',30)  # outside 7 but in 15
            for site in ('52112','52203','51495'): add(model,24,1,site,'t',0)
            add(model,24,1,'A','rain24',10,1)  # obs light/forecast moderate => B2 only
            add(model,24,2,'A','rain24',0,0,1)
            add(model,24,3,'A','rain24',.1,0,1)  # trace fixes PC
            add(model,48,1,'B','rain24',60 if extreme else 20,300 if extreme else 0)
            add(model,72,1,'B','rain24',100 if extreme else 1,0)
            for site in ('52112','52203','51495'): add(model,24,1,site,'rain24',300,300)
        db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?)',pairs)
        db.commit(); db.close()
        # Hand-built equivalent of the offline per-lead score product.
        for days in (3,7,15):
            rows = []
            for model in ('ec01','gfs'):
                for variable in ('t','rain24'):
                    for lead in sorted({p[2] for p in pairs}):
                        group = [p for p in pairs if p[0]==model and p[5]==variable and p[2]==lead
                                 and p[4] in ('A','B') and stamp(p[3])>=stamp(end)-timedelta(days=days)]
                        if not group: continue
                        fc,ob = [p[6] for p in group],[p[7] for p in group]
                        base = dict(model=model,var=variable,lead=lead,n=len(group))
                        if variable=='t': rows.append(dict(base,MAE=sum(abs(f-o) for f,o in zip(fc,ob))/len(group),**guifan.accuracy(fc,ob)))
                        else: rows.extend(guifan.rain_rows(base,fc,ob,24,[p[8] for p in group]))
            (directory/f'scores-{days}.json').write_text(json.dumps(rows))
        (self.root/'current.json').write_text(json.dumps(dict(generation=generation)))
        return directory

    def request(self, **params):
        return api.dispatch('overview','&'.join(f'{k}={v}' for k,v in params.items()))

    def test_count_pooling_day_boundaries_and_window(self):
        for day, expected in ((1,(4,1,1,2.5)),(2,(2,0,1,3)),(3,(2,1,2,1))):
            row = self.request(element='t',day=day,days=7)['rows'][0]
            self.assertEqual((row['n'],row['N1'],row['N2'],row['MAE']),expected)
            self.assertEqual(row['ACC2'],expected[2]/expected[0])
        self.assertEqual(self.request(element='t',day=1,days=15)['rows'][0]['n'],5)
        result = self.request(element='t')
        self.assertEqual(result['start'],'2026-11-02T08:00:00')
        self.assertEqual(result['end'],'2026-11-09T08:00:00')

    def test_grade_absence_zero_trace_and_observation_counts(self):
        result = self.request()
        self.assertEqual([g['id'] for g in result['grades']],[1,2,3,4])
        self.assertEqual([g['observed'] for g in result['grades']],[1,0,0,0])
        self.assertEqual(result['rows'][0]['grades'][1]['TS'],0)
        self.assertIsNone(result['rows'][0]['grades'][2]['TS'])
        self.assertAlmostEqual(result['rows'][0]['PC'],1)
        # Observations counted once across models, not inferred as A+C.
        self.assertEqual(result['rows'][0]['grades'][0]['A']+result['rows'][0]['grades'][0]['C'],0)
        self.assertEqual(self.request(day=2)['rows'][0]['n'],1)

    def test_cumulative_thresholds(self):
        # 2026-11-18 累加检验：预报、实况都达到量级算报对。
        result = self.request()
        row = result["rows"][0]
        self.assertEqual([(c["threshold"],c["A"],c["B"],c["C"]) for c in row["cumulative"][:3]],
                         [(.1,1,1,0),(10,0,1,0),(25,0,0,0)])
        self.assertEqual(row["cumulative"][0]["TS"],.5)
        self.assertEqual(row["cumulative"][1]["TS"],0)
        self.assertIsNone(row["cumulative"][2]["TS"])
        self.assertEqual([g["observed_cumulative"] for g in result["grades"]],[1,0,0,0])
        self.assertEqual([g["threshold"] for g in result["grades"]],[.1,10,25,50])
        # 现算路径（没有评分文件）结果相同。
        for path in self.directory.glob("scores-*.json"): path.unlink()
        api.overview_cached.cache_clear(); api.cached_json.cache_clear()
        self.assertEqual(self.request()["rows"][0]["cumulative"],row["cumulative"])

    def test_extreme_columns_observed_and_forecast_even_underforecast(self):
        self.make_snapshot('20261109T000001',extreme=True)
        self.assertEqual([g['id'] for g in self.request(day=1)['grades']],[1,2,3,4])
        # Forecast 60 / observed 300 -> only extreme-observation column appears.
        self.assertEqual([g['id'] for g in self.request(day=2)['grades']],[1,2,3,4,6])
        # Forecast 100 / observed dry -> heavy-forecast column appears.
        self.assertEqual([g['id'] for g in self.request(day=3)['grades']],[1,2,3,4,5])

    def test_south_only_actual_station_count_and_order(self):
        result = self.request(element='t')
        self.assertEqual(result['station_count'],2)
        self.assertEqual([r['model'] for r in result['rows']],['ec01','gfs'])
        self.assertEqual(result['rows'][0]['n'],4)
        self.assertEqual(self.request()['station_count'],1)

    def test_fallback_matches_scores_and_excludes_xinjiang(self):
        for element in ('t','rain'):
            for day in (1,2,3):
                expected = self.request(element=element,day=day)
                api.overview_cached.cache_clear()
                with patch.object(api,'cached_json',side_effect=lambda path: [] if '/scores-' in path else json.loads(Path(path).read_text())):
                    actual = self.request(element=element,day=day)
                self.assertEqual(actual,expected)
                api.overview_cached.cache_clear()

    def test_generation_cache_read_only_and_fast_hit(self):
        result = self.request()
        before_day2 = self.request(day=2)
        self.assertEqual(len(before_day2['grades']),4)
        with patch.object(api.sqlite3,'connect',side_effect=AssertionError('cached call opened DB')):
            started = time.monotonic()
            for _ in range(50): self.assertEqual(self.request(),result)
            self.assertLess(time.monotonic()-started,1)
        self.make_snapshot('20261109T000002',extreme=True)
        changed = self.request(day=2)
        self.assertNotEqual(changed,before_day2)
        self.assertEqual(changed['generation'],'20261109T000002')
        self.assertEqual(changed['grades'][-1]['id'],6)
        self.assertFalse((self.root/'pairs.sqlite').exists())
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()),
                         ['meta.json','pairs.sqlite','scores-15.json','scores-3.json','scores-7.json'])

    def test_forecast_grade_can_be_hidden_by_table31_underforecast(self):
        directory = self.make_snapshot('20261109T000003',extreme=True)
        with sqlite3.connect(directory/'pairs.sqlite') as db:
            db.execute("UPDATE pairs SET fc=150 WHERE var='rain24' AND lead=48")
        result = self.request(day=2)
        self.assertEqual([g['id'] for g in result['grades']],[1,2,3,4,5,6])
        self.assertEqual(result['grades'][-2]['observed'],0)

    def test_read_only_http_and_static_asset(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        import server
        httpd = ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        worker = threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        before = {p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in self.directory.iterdir()}
        try:
            for path, status in (('api/stations/overview?element=t&day=2&days=7',200),
                                 ('api/stations/overview?days=99',400),('overview.js',200)):
                connection = http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
                connection.request('GET','/Verification/test/'+path)
                response = connection.getresponse();body=response.read()
                self.assertEqual(response.status,status)
                if path=='overview.js': self.assertIn(b'AssessmentOverview',body)
                elif status==200: self.assertEqual(json.loads(body)['rows'][0]['N2'],1)
                connection.close()
        finally:
            httpd.shutdown();httpd.server_close();worker.join()
        self.assertEqual(before,{p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in self.directory.iterdir()})

    def test_model_order_is_platform_order_and_omits_unpaired_models(self):
        directory = self.make_snapshot('20261109T000004')
        with sqlite3.connect(directory/'pairs.sqlite') as db:
            db.execute("INSERT INTO pairs VALUES ('ecmwf','2026-11-07T00:00:00Z',24,'2026-11-08T00:00:00Z','A','t',90,0,0)")
        (directory/'scores-7.json').unlink()  # exercise SQLite fallback
        result = self.request(element='t')
        self.assertEqual([r['model'] for r in result['rows']],['ecmwf','ec01','gfs'])
        self.assertEqual(result['rows'][0]['ACC2'],0)
        self.assertNotIn('wn3',{r['model'] for r in result['rows']})

    def test_validation_and_empty_snapshot(self):
        for query in ('element=td','day=0','day=4','days=1','days=8','day=1&day=2','extra=x','day='):
            with self.assertRaises(ValueError): api.dispatch('overview',query)
        (self.root/'current.json').unlink()
        self.assertEqual(self.request()['state'],'empty')


if __name__=='__main__': unittest.main()
