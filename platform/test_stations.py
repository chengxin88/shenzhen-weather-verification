"""Station decoder, exact windows, QC, pairing, persisted maths and HTTP isolation."""
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
import csv
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import numpy as np
from station_common import UTC, atomic_json, iso
from synop_decode import decode, latest_reports, precipitation
from station_forecasts import accumulate, interpolate, interpolate_dewpoint, read_native_frame, native_signature, forecast_runs
from station_obs import row, qc_rows, select_observations, ghcnh_rows, deduplicate
from station_score import continuous, contingency, pair_run, database, stats_sql, aggregates
import station_api
import server
import station_sources
import station_score

REAL = [
 '59287,2026,10,25,09,00,AAXX 25091 59287 14/80 /0502 10280 20184 30048 40131 52003 60001 333 10297 20196 3/019 58055 70000 90761 91108==',
 '59287,2026,10,25,12,00,AAXX 25121 59287 14/80 /0302 10249 20171 30072 40156 52024 60001 333 10297 20196 3/025 58057 70000 90761 91108=='
]


def line(groups='', mandatory='14/80 /0502', clock='25091'):
    return f'59287,2026,10,25,09,00,AAXX {clock} 59287 {mandatory} {groups}=='


class SynopTests(unittest.TestCase):
    def test_real_examples_and_section_boundaries(self):
        a,b=map(decode,REAL)
        self.assertEqual(a['values']['t'],28)
        self.assertEqual(a['values']['td'],18.4)
        self.assertEqual(a['values']['msl'],1013.1)
        self.assertEqual(b['values']['msl'],1015.6)
        self.assertEqual(a['values']['wind'],2)
        self.assertEqual([(p['hours'],p['value']) for p in a['precip']],[(6,0),(24,0)])
        self.assertEqual(a['tmax'],29.7);self.assertEqual(a['tmin'],19.6)
        self.assertEqual(b['values']['t'],24.9)

    def test_knots_extended_wind_negative_and_missing(self):
        r=decode(line('00125 11025 21063 49875',mandatory='14/80 /1899',clock='25094'))
        self.assertAlmostEqual(r['values']['wind'],125*.5144444444444445)
        self.assertEqual(r['values']['t'],-2.5);self.assertEqual(r['values']['td'],-6.3)
        self.assertEqual(r['values']['msl'],987.5)
        missing=decode(line('1//// 2//// 4//// 6////',mandatory='///// /////',clock='2509/'))
        self.assertIsNone(missing['values']['t']);self.assertNotIn('wind',missing['values'])
        self.assertIsNone(missing['precip'][0]['hours'])

    def test_every_precipitation_period_and_amount_boundary(self):
        periods={1:6,2:12,3:18,4:24,5:1,6:2,7:3,8:9,9:15,0:None}
        for code,hours in periods.items():
            self.assertEqual(precipitation('6012'+str(code))['hours'],hours)
        self.assertIsNone(precipitation('6012/')['hours'])
        for amount,value in [('000',0),('988',988),('989',989),('990',0),('991',.1),('999',.9)]:
            p=precipitation('6'+amount+'1');self.assertEqual(p['value'],value)
            self.assertEqual(p['trace'],amount=='990');self.assertEqual(p['lower_bound'],amount=='989')

    def test_section333_trace_and_ir3_ir4(self):
        report=decode(line('69901 333 79999'))
        self.assertEqual([(p['hours'],p['trace'],p['value']) for p in report['precip']],[(6,True,0),(24,True,0)])
        unknown=decode(line('10200',mandatory='34/80 /0000'))
        self.assertEqual(unknown['precip'][0]['value'],0);self.assertIsNone(unknown['precip'][0]['hours'])
        absent=decode(line('60001 333 70000',mandatory='44/80 /0000'))
        self.assertEqual(absent['precip'],[])
        self.assertEqual(len(absent['unobserved_precip_groups']),2)

    def test_section333_radiation_groups_are_not_precip_or_tmin(self):
        r=decode(line('60001 333 10297 20196 55300 01234 20456 60789 58055 70012'))
        self.assertEqual(r['tmin'],19.6)
        self.assertEqual(r['radiation_groups'],['01234','20456','60789'])
        self.assertEqual([(p['section'],p['hours'],p['value']) for p in r['precip']],[(1,6,0),(333,24,1.2)])

    def test_rh_high_station_nil_and_corrections(self):
        report=decode(line('10200 29050 48520'))
        self.assertAlmostEqual(report['values']['td'],9.26,places=1)
        self.assertNotIn('msl',report['values']);self.assertEqual(report['geopotential_group'],'48520')
        self.assertTrue(decode('59287,2026,10,25,09,00,AAXX 25091 59287 NIL=')['nil'])
        corrected=REAL[0].replace('10280','10270').replace('AAXX','CCA AAXX')
        latest=latest_reports([corrected,REAL[0],corrected.replace('CCA','CCB').replace('10270','10260')])
        self.assertEqual(len(latest),1);self.assertEqual(latest[0]['values']['t'],26)
        self.assertEqual(decode(REAL[0].replace('AAXX','AAXX CCA'))['values']['t'],28)
        with self.assertRaises(ValueError):decode(REAL[0].replace('25091','25121'))
        with self.assertRaises(ValueError):decode(REAL[0].replace('25091 59287','25091 59493')+' 59287 14/80 /0502')


class WindowPairMathTests(unittest.TestCase):
    def test_exact_native_three_and_six_hour_windows(self):
        parts={h:(h-3,np.array([1.,2.])) for h in range(3,25,3)}
        np.testing.assert_equal(accumulate(parts,24,6),[2,4])
        np.testing.assert_equal(accumulate(parts,24,24),[8,16])
        self.assertIsNone(accumulate({k:v for k,v in parts.items() if k!=12},24,24))
        parts={h:(h-6,np.array([2.])) for h in range(6,25,6)}
        np.testing.assert_equal(accumulate(parts,24,24),[8])
        self.assertIsNone(accumulate(parts,24,3))
        self.assertIsNone(accumulate({6:(1,np.array([0]))},6,6))

    def test_openmeteo_steps_across_90_and_144(self):
        parts={h:(h-1,np.array([1.])) for h in range(1,91)}
        parts.update({h:(h-3,np.array([3.])) for h in range(93,145,3)})
        parts.update({h:(h-6,np.array([6.])) for h in range(150,169,6)})
        for end,hours in [(90,6),(93,6),(96,24),(144,6),(150,24),(168,24)]:
            np.testing.assert_equal(accumulate(parts,end,hours),[hours])
        self.assertIsNone(accumulate(parts,93,1));self.assertIsNone(accumulate(parts,5,6))
        del parts[90];self.assertIsNone(accumulate(parts,93,6))

    def test_bilinear_nearest_outside_dewpoint(self):
        a=np.array([[0.,10.],[20.,30.]])
        np.testing.assert_equal(interpolate(a,[108,109],[18,19],[(108.25,18.25)]),[7.5])
        np.testing.assert_equal(interpolate(a,[108,109],[18,19],[(108.25,18.25)],True),[0])
        self.assertTrue(np.isnan(interpolate(a,[108,109],[18,19],[(110,20)])[0]))
        td=interpolate_dewpoint(np.full((2,2),20.),np.full((2,2),50.),[0,1],[0,1],[(.5,.5)])
        self.assertAlmostEqual(td[0],9.26,places=1)

    def test_metar_tolerance_tie_and_variable_specific_fallback(self):
        at='2026-10-25T09:00:00Z'; stations=[dict(id='ZGGG',type='metar'),dict(id='59287',type='synop')]
        rows=[row('ZGGG','2026-10-25T08:50:00Z','t',20,'iem'),row('ZGGG','2026-10-25T09:10:00Z','t',22,'iem'),
              row('ZGGG','2026-10-25T09:11:00Z','td',20,'iem'),row('59287',at,'t',20,'ghcnh'),
              row('59287',at,'t',21,'ogimet'),row('59287',at,'td',19,'ghcnh')]
        chosen=select_observations(rows,stations)
        self.assertEqual(chosen['ZGGG',at,'t']['value'],20)
        self.assertNotIn(('ZGGG',at,'td'),chosen)
        self.assertEqual(chosen['59287',at,'t']['value'],21)
        self.assertEqual(chosen['59287',at,'td']['source'],'ghcnh')
        rows+=[row('ZGGG',at,'wind',2,'iem')]
        chosen=select_observations(rows,stations)
        self.assertNotIn(('ZGGG',at,'t'),chosen)
        self.assertEqual(chosen['ZGGG',at,'wind']['value'],2)

    def test_qc_and_unscored_unknown_precip(self):
        at='2026-10-25T09:00:00Z'
        rows=qc_rows([row('a',at,'t',20,'ogimet'),row('a',at,'td',21,'ogimet'),
                      row('b',at,'t',60,'ogimet'),row('c',at,'rain6',600,'ogimet'),
                      row('d',at,'t',20,'ogimet'),row('d','2026-10-25T10:00:00Z','t',44,'ogimet')])
        self.assertIn('dewpoint_above_temperature',rows[1]['flags'])
        self.assertIn('physical_range',rows[2]['flags']);self.assertIn('physical_range',rows[3]['flags'])
        self.assertIn('temporal_jump',rows[-1]['flags'])
        duplicate=deduplicate([row('a',at,'t',20,'ghcnh'),row('a',at,'t',21,'ghcnh')])
        self.assertEqual(len(duplicate),1);self.assertIn('duplicate_conflict',duplicate[0]['flags'])

    def test_pairing_has_no_invented_variables_or_pre_run_rain(self):
        run=datetime(2026,10,25,tzinfo=UTC);valid=iso(run+timedelta(hours=6))
        stations=[dict(id='a',type='synop')];obs={('a',valid,'t'):row('a',valid,'t',20,'ogimet'),
                                             ('a',valid,'rain6'):row('a',valid,'rain6',2,'ogimet')}
        samples={3:{'rain':np.array([1]),'rain_start':0},6:{'t':np.array([21]),'rain':np.array([1]),'rain_start':3}}
        pairs=list(pair_run('ecmwf',run,samples,obs,stations,run,run+timedelta(hours=10)))
        self.assertEqual({r[5] for r in pairs},{'t','rain6'});self.assertEqual(pairs[-1][6:8],(2.,2))
        self.assertEqual(list(pair_run('ecmwf',run,{6:samples[6]},obs,stations,run,run+timedelta(hours=10)))[0][5],'t')

    def test_pooled_scores_sql_and_zero_denominators(self):
        fc=[1,2,4];ob=[1,3,2];stats=continuous(fc,ob)
        self.assertAlmostEqual(stats['ME'],1/3);self.assertAlmostEqual(stats['RMSE'],math.sqrt(5/3))
        self.assertIsNone(continuous([1,1],[2,2])['CC'])
        c=contingency([0,1,2,3],[0,2,0,3],1)
        self.assertEqual((c['H'],c['M'],c['F'],c['C']),(2,0,1,1));self.assertAlmostEqual(c['TS'],2/3)
        self.assertIsNone(contingency([0],[0],1)['TS'])
        with tempfile.TemporaryDirectory() as temporary:
            db=database(Path(temporary)/'test.sqlite')
            db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                          [('ec01','r',i,'v','s','t',f,o,'ogimet',0,'v') for i,(f,o) in enumerate(zip(fc,ob))])
            result=stats_sql(db,['model'])[0]
            for key in ['n','ME','MAE','RMSE','CC']:self.assertAlmostEqual(result[key],stats[key])
            db.close()

    def test_source220_removed_quality_code_never_scores(self):
        with tempfile.TemporaryDirectory() as temporary:
            raw=Path(temporary);(raw/'ghcnh').mkdir()
            fields=['STATION','DATE','temperature','temperature_Quality_Code','temperature_Source_Code']
            with (raw/'ghcnh/CHM00059287_2026.psv').open('w') as handle:
                writer=csv.DictWriter(handle,fields,delimiter='|');writer.writeheader()
                writer.writerow(dict(zip(fields,['CHM00059287','2026-10-25T09:00:00','20','5','220'])))
            rows,_=ghcnh_rows(raw,[dict(id='59287',ghcnh_id='CHM00059287')],datetime(2026,10,1,tzinfo=UTC),datetime(2026,10,26,tzinfo=UTC))
            self.assertEqual(rows[0]['flags'],['ghcnh_quality_5'])

    def test_native_reader_preserves_shared_box_and_interpolates_wind_components(self):
        import native_archive as shared
        original=shared.BOX
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'ecmwf_2026102500_f003_global1.json';fields=path.with_suffix('.fields');fields.mkdir()
            x=np.arange(108,120.01,.25);y=np.arange(18,27.01,.25)
            metadata=dict(schema=2,model='ECMWF-IFS',run='2026-10-25T00:00:00Z',valid='2026-10-25T03:00:00Z',lead=3,
                          storage='ecmwf-regional-v1',native_grid_degrees=.25,bounds=[108.,18.,120.,27.],
                          lon=x.tolist(),lat=y.tolist(),rain_start='2026-10-25T00:00:00Z',interval_hours=3)
            path.write_text(json.dumps(metadata));shape=(len(y),len(x))
            for name,value in [('surface_t',20),('surface_rh',50),('surface_msl',1010),('surface_v',0),('rain3h',1.5)]:
                np.save(fields/(name+'.npy'),np.full(shape,value,dtype=np.float32))
            u=np.full(shape,10,dtype=np.float32);u[:,1]=-10;np.save(fields/'surface_u.npy',u)
            result=read_native_frame(path,'ecmwf',datetime(2026,10,25,tzinfo=UTC),3,[(108.125,18.125),(121,19)])
            self.assertEqual(result['wind'][0],0);self.assertAlmostEqual(result['td'][0],9.26,places=1)
            self.assertEqual(result['rain'][0],1.5);self.assertTrue(np.isnan(result['t'][1]))
            self.assertEqual(shared.BOX,original)
            signature,data,meta_stat=native_signature(path)
            saved=dict(path=str(path),metadata=data,meta_stat=meta_stat)
            with patch.object(Path,'read_text',side_effect=AssertionError('unchanged metadata should be cached')):
                self.assertEqual(native_signature(path,saved)[0],signature)
            # A field replacement invalidates samples even when JSON is unchanged.
            before=(fields/'surface_t.npy').stat().st_mtime_ns
            np.save(fields/'surface_t.npy',np.full(shape,21,dtype=np.float32))
            # Same size, and a fast rewrite can land on the same coarse mtime tick; real frames change minutes apart.
            os.utime(fields/'surface_t.npy',ns=(before+10**9,before+10**9))
            self.assertNotEqual(native_signature(path,saved)[0],signature)
            # Once certified, points remain usable when immutable raw frames
            # are pruned. Moving the station must invalidate that certificate.
            cache=Path(temporary)/'cache';cache.mkdir()
            at=datetime(2026,10,25,3,tzinfo=UTC);run=at-timedelta(hours=3)
            stations=[dict(id='x',lon=108.125,lat=18.125)]
            with patch('station_forecasts.discover',return_value={('ecmwf',iso(run)):{3:path}}):
                first=next(forecast_runs(cache,stations,at))
                path.unlink()
                second=next(forecast_runs(cache,stations,at))
                self.assertEqual(first[3],second[3]);self.assertEqual(second[2][3]['t'][0],21.)
                moved=next(forecast_runs(cache,[dict(id='x',lon=109.,lat=18.125)],at))
                self.assertEqual(moved[2],{})


class StationHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.base=f'http://127.0.0.1:{cls.http.server_port}/Verification/test/'
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'STATIONS_CACHE_DIR':str(self.root)});self.env.start()
        directory=self.root/'snapshots/20261025T000000';directory.mkdir(parents=True)
        atomic_json(self.root/'current.json',dict(generation='20261025T000000'))
        atomic_json(directory/'meta.json',dict(schema=1,generated='2026-10-25T12:00:00Z',window_end='2026-10-25T12:00:00Z',
                    stations=[dict(id='59287',type='synop')],sources={},models=[]))
        atomic_json(directory/'scores-30.json',[dict(model='ec01',var='t',lead=24,n=1,ME=1,MAE=1,RMSE=1,CC=None)])
        atomic_json(directory/'station-scores-30.json',[])
        db=database(directory/'pairs.sqlite');db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  ('ec01','2026-10-24T09:00:00Z',24,'2026-10-25T09:00:00Z','59287','t',21,20,'ogimet',0,'2026-10-25T09:00:00Z'))
        db.execute('CREATE TABLE detail_stats(station,model,var,lead,days,stats)');db.commit();db.close()

    def tearDown(self):
        station_api.cached_json.cache_clear();self.env.stop();self.tmp.cleanup()

    def request(self,path,body=None):
        try:
            with urlopen(Request(self.base+path,data=body),timeout=5) as response:return response.status,response.read()
        except HTTPError as error:return error.code,error.read()

    def test_routes_only_read_persisted_results(self):
        before={str(p.relative_to(self.root)):(p.stat().st_mtime_ns,p.stat().st_size) for p in self.root.rglob('*') if p.is_file()}
        with patch.object(server,'rebuild',side_effect=AssertionError('rebuild')),patch('requests.sessions.Session.request',side_effect=AssertionError('network')):
            for path in ['meta','scores?var=t&models=ec01,ecmwf&days=30','station?id=59287&var=t&model=ec01&lead=24']:
                status,body=self.request('api/stations/'+path);self.assertEqual(status,200);self.assertNotIn(b'NaN',body)
            self.assertEqual(json.loads(self.request('api/stations/station?id=59287')[1])['series'][0]['ob'],20)
        after={str(p.relative_to(self.root)):(p.stat().st_mtime_ns,p.stat().st_size) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_day_windows_follow_published_files(self):
        self.assertEqual(json.loads(self.request('api/stations/meta')[1])['windows'],[30])
        status,body=self.request('api/stations/scores?var=t&days=1')
        self.assertEqual(status,400);self.assertIn('下次定时评分',json.loads(body)['error'])
        snapshot=self.root/'snapshots/20261025T000000'
        atomic_json(snapshot/'scores-1.json',[dict(model='ec01',var='t',lead=6,n=1,ME=0,MAE=0,RMSE=0,CC=None)])
        atomic_json(snapshot/'station-scores-1.json',[])
        self.assertEqual(json.loads(self.request('api/stations/meta')[1])['windows'],[1,30])
        status,body=self.request('api/stations/scores?var=t&models=ec01&days=1')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['rows'][0]['lead'],6)
        self.assertEqual(self.request('api/stations/scores?days=2')[0],400)

    def test_invalid_queries_post_and_raw_cache_are_rejected(self):
        for path in ['meta?refresh=1','scores?days=999','scores?var=t&var=td','scores?models=ec01,ec01',
                     'station?id=../../private','station?id=59287&lead=-1','scores?path=/etc/passwd']:
            self.assertEqual(self.request('api/stations/'+path)[0],400,path)
        self.assertEqual(self.request('api/stations/refresh')[0],404)
        self.assertEqual(self.request('api/stations/meta',b'{}')[0],404)
        self.assertEqual(self.request('cache/stations/current.json')[0],404)

    def test_preview_refresh_cannot_rebuild_any_catalog(self):
        with patch.dict(os.environ,{'VERIFICATION_READ_ONLY':'1','VERIFICATION_PREVIEW_DATA_ROOT':str(self.root)}),patch.object(server,'build_catalog',side_effect=AssertionError):
            self.assertEqual(self.request('api/catalog?refresh=1')[0],200)
            self.assertEqual(self.request('api/quality-control/refresh',b'{}')[0],405)

    def test_assets_and_module_order(self):
        for suffix in ['stations.js','stations.css']:
            self.assertEqual(self.request(suffix)[0],200)
        page=self.request('')[1].decode()
        self.assertIn('data-module="stations"',page);self.assertIn('id="stations-workspace"',page)
        self.assertLess(page.index('./stations.js'),page.index('./modules.js'))


class DownloadAndIncrementalTests(unittest.TestCase):
    def test_explicit_proxy_does_not_inherit_metered_link(self):
        with patch.dict(os.environ,{'HTTPS_PROXY':'http://127.0.0.1:7897'}):
            client=station_sources.session()
            self.assertFalse(client.trust_env)
            self.assertEqual(client.proxies['https'],'http://127.0.0.1:17908')
            client.close()

    def test_ogimet_error_latch_and_cross_request_spacing(self):
        start=datetime(2026,10,1,tzinfo=UTC);stop=start+timedelta(days=1)
        good=MagicMock();good.__enter__.return_value=good
        good.iter_content.return_value=[REAL[0].encode()]
        bad=MagicMock();bad.__enter__.return_value=bad
        bad.iter_content.return_value=[b'<html>Too many requests</html>']
        client=MagicMock();client.get.side_effect=[good,bad]
        with tempfile.TemporaryDirectory() as temporary,patch.object(station_sources.time,'time',return_value=100),patch.object(station_sources.time,'sleep') as sleep:
            root=Path(temporary);station_sources.ogimet(client,root,start,stop,True)
            journal=json.loads((root/'ogimet-journal.json').read_text())
            self.assertEqual(len(journal['requests']),2);self.assertIsNotNone(journal['stopped'])
            sleep.assert_called_once_with(20.5)
            station_sources.ogimet(client,root,start,stop,True)
            self.assertEqual(client.get.call_count,2)
            self.assertEqual(len(list((root/'raw/ogimet').glob('*.txt'))),1)

    def test_flock_prevents_second_refresh_before_any_network(self):
        import fcntl,subprocess,sys
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with (root/'refresh.lock').open('a') as handle:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                result=subprocess.run([sys.executable,'refresh_stations.py','--fetch-only'],
                    env={**os.environ,'STATIONS_CACHE_DIR':temporary},capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0);self.assertIn('flock',result.stdout)
            self.assertEqual([p.name for p in root.iterdir()],['refresh.lock'])

    def test_source_or_observation_change_alone_invalidates_run(self):
        end=datetime(2026,10,25,9,tzinfo=UTC);run=end-timedelta(hours=6)
        station=dict(id='59287',type='synop',name='test',lat=23.,lon=113.,elevation=10,sources=['ogimet'])
        at=iso(end);obs={('59287',at,'t'):row('59287',at,'t',20,'ogimet')}
        samples={6:{'t':np.array([21.])}};source=[('ec01',run,samples,'source-a',[])]
        with tempfile.TemporaryDirectory() as temporary,patch.object(station_score,'forecast_runs',side_effect=lambda *args:iter(source)),\
             patch.object(station_score,'load_observations',side_effect=lambda *args:(obs,{},{})),\
             patch.object(station_score,'shenzhen_sanity',return_value={'n':0}):
            root=Path(temporary);atomic_json(root/'stations.json',{'stations':[station]})
            first=station_score.refresh(root,end);second=station_score.refresh(root,end)
            self.assertEqual(first['processed_runs'],1);self.assertEqual(second['processed_runs'],0)
            obs['59287',at,'t']['value']=19
            third=station_score.refresh(root,end);self.assertEqual(third['processed_runs'],1)
            source[0]=('ec01',run,samples,'source-b',[])
            self.assertEqual(station_score.refresh(root,end)['processed_runs'],1)
            with sqlite3.connect(root/'working.sqlite') as db:
                self.assertEqual(db.execute('SELECT count(*),ob FROM pairs').fetchone(),(1,19.))
            # Four refreshes published four generations; only the newest two remain, each with all day windows.
            kept=sorted((root/'snapshots').iterdir());self.assertEqual(len(kept),2)
            self.assertEqual(kept[-1].name,json.loads((root/'current.json').read_text())['generation'])
            for days in (1,3,7,30):self.assertTrue((kept[-1]/f'scores-{days}.json').is_file(),days)


import math
if __name__=='__main__':unittest.main()
