"""NMIC imports, conservative source priority and forecast expansion (offline)."""
from datetime import datetime, timedelta
from pathlib import Path
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from station_common import UTC, atomic_json, digest, iso
from station_obs import row, qc_rows, select_observations
from station_forecasts import accumulate, forecast_runs, load_samples, save_samples
from station_score import continuous, pair_run
import nmic_obs as nmic
import nmic_ec01 as ec
import station_api

FIXTURE = Path(__file__).parent / 'tests/fixtures/nmic_sample.txt'


class NMIC(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.folder = self.root/'raw/nmic'; self.folder.mkdir(parents=True)
        self.header = FIXTURE.read_text().splitlines()[0].split()
        self.item = dict(zip(self.header, FIXTURE.read_text().splitlines()[1].split()))
        self.sid = self.item['Station_Id_C']; self.at = datetime(2026, 11, 3, 3, tzinfo=UTC)
        self.station = dict(id=self.sid, type='synop', name='test', lon=113., lat=23., sources=['nmic'])
        self.env = patch.dict(os.environ, {'STATIONS_NMIC_DIR':str(self.folder),
                                         'STATIONS_NMIC_METADATA':str(self.folder/'A0012_stations_api.json')})
        self.env.start(); self.addCleanup(self.env.stop)

    def write(self, name='a.txt', **changes):
        data = {**self.item, **{k:str(v) for k,v in changes.items()}}
        path = self.folder/name
        path.write_text(' '.join(self.header)+'\n'+' '.join(data[k] for k in self.header)+'\n')
        return data

    def records(self, amounts):
        return {(self.sid,self.at+timedelta(hours=3*i)):{**self.item,'PRE_3h':str(a)} for i,a in enumerate(amounts)}

    def rows(self, records):
        return nmic.observation_rows([self.station], self.at+timedelta(days=2), self.at, records=records)

    def test_sample_parser_utc_units_and_dewpoint(self):
        records, diag = nmic.read_records(FIXTURE.parent)
        key = next(iter(records)); item = records[key]
        rows = nmic.observation_rows([dict(id=key[0])], key[1], key[1], records=records)
        values = {r['var']:r['value'] for r in rows}
        self.assertEqual(values['t'], float(item['TEM']))
        self.assertEqual(values['wind'], float(item['WIN_S_Avg_2mi']))
        self.assertEqual(values['msl'], float(item['PRS_Sea']))
        self.assertAlmostEqual(values['td'], nmic._dewpoint(values['t'],float(item['RHU'])))
        self.assertTrue(all(r['time'].endswith('Z') for r in rows))
        self.assertEqual(diag['records'],16)

    def test_missing_unobserved_reserved_nonfinite_and_invalid_humidity(self):
        for code in ('999999','999998','999990','999800.1','nan','inf','/'):
            self.assertIsNone(nmic.number(code))
        for rh in ('0','101','999999'):
            records = {(self.sid,self.at):{**self.item,'TEM':'999998','RHU':rh,'PRS_Sea':'999999','WIN_S_Avg_2mi':'nan'}}
            self.assertEqual(self.rows(records),[])
            records[self.sid,self.at]['TEM'] = '20'
            self.assertNotIn('td', {r['var'] for r in self.rows(records)})
        self.assertEqual(nmic._dewpoint(20,100),20)

    def test_exact_accumulation_gaps_cross_file_boundary_and_cutoff(self):
        records = self.records(range(1,9)); final = self.at+timedelta(hours=21)
        values = {r['var']:r['value'] for r in self.rows(records) if r['time']==iso(final)}
        self.assertEqual([values[v] for v in ('rain6','rain12','rain24')],[15,26,36])
        # Support data before cutoff are still used in the window.
        rows = nmic.observation_rows([self.station], final, final, records=records)
        self.assertEqual(next(r['value'] for r in rows if r['var']=='rain24'),36)
        for code in ('999999','999998','999800.1','-1','nan'):
            bad = copy.deepcopy(records); bad[self.sid,final]['PRE_3h']=code
            self.assertFalse(any(r['var'].startswith('rain') and r['time']==iso(final) for r in self.rows(bad)))
        del records[self.sid,self.at+timedelta(hours=6)]
        self.assertNotIn('rain24',{r['var'] for r in self.rows(records) if r['time']==iso(final)})

    def test_trace_zero_and_mixed_amounts(self):
        records = self.records([999990,0,999990,0,0,0,0,0]); final=self.at+timedelta(hours=21)
        total = next(r for r in self.rows(records) if r['var']=='rain24' and r['time']==iso(final))
        self.assertEqual(total['value'],0);self.assertTrue(total['trace'])
        records[self.sid,final]['PRE_3h']='0.1'
        total = next(r for r in self.rows(records) if r['var']=='rain24' and r['time']==iso(final))
        self.assertEqual(total['value'],.1);self.assertFalse(total['trace'])

    def test_deduplication_filename_order_and_header_validation(self):
        self.write('b.txt',TEM=30);self.write('a.txt',TEM=20)
        records,diag=nmic.read_records(self.folder)
        self.assertEqual(len(records),1);self.assertEqual(diag['duplicates'],1);self.assertEqual(diag['conflicts'],1)
        self.assertEqual(next(iter(records.values()))['TEM'],'30')
        self.write('c.txt',Hour=6)
        rows=nmic.observation_rows([self.station],self.at+timedelta(hours=3),self.at,self.root)
        self.assertTrue(any(r['var']=='rain6' for r in rows))
        (self.folder/'broken.txt').write_text('Station_Id_C Year\n')
        with self.assertRaisesRegex(ValueError,'header'):nmic.read_records(self.folder)

    def test_all_legacy_sources_and_derived_rain12_have_priority(self):
        for source in ('ogimet','ghcnh','iem','szopen','szgrid'):
            existing = [row(self.sid,iso(self.at),'t',20.,source),
                        row(self.sid,iso(self.at),'rain6',2.,source),
                        row(self.sid,iso(self.at-timedelta(hours=6)),'rain6',3.,source)]
            before=select_observations(copy.deepcopy(existing),[self.station])
            extra=[row(self.sid,iso(self.at),'t',30.,'nmic'),row(self.sid,iso(self.at),'rain12',9.,'nmic'),
                   row(self.sid,iso(self.at),'td',10.,'nmic')]
            after=select_observations(copy.deepcopy(existing+extra),[self.station])
            self.assertTrue(all(after[k]==v for k,v in before.items()),source)
            self.assertEqual(after[self.sid,iso(self.at),'td']['source'],'nmic')
        bad=row(self.sid,iso(self.at),'t',30.,'nmic');bad['flags']=['physical_range']
        self.assertEqual(select_observations([bad],[self.station]),{})

    def test_nmic_is_primary_inside_three_provinces(self):
        station=dict(self.station,province_code='44')
        for source in ('ogimet','ghcnh','szopen'):
            existing=[row(self.sid,iso(self.at),'t',20.,source),row(self.sid,iso(self.at),'msl',1010.,source),
                      row(self.sid,iso(self.at-timedelta(hours=3)),'t',19.,source)]
            extra=[row(self.sid,iso(self.at),'t',30.,'nmic'),row(self.sid,iso(self.at),'rain12',9.,'nmic')]
            after=select_observations(copy.deepcopy(existing+extra),[station])
            self.assertEqual((after[self.sid,iso(self.at),'t']['source'],after[self.sid,iso(self.at),'t']['value']),('nmic',30.),source)
            self.assertEqual(after[self.sid,iso(self.at),'rain12']['source'],'nmic')
            self.assertEqual(after[self.sid,iso(self.at),'msl']['source'],source)
            self.assertEqual(after[self.sid,iso(self.at-timedelta(hours=3)),'t']['source'],source)

    def test_station_merge_preserves_metadata_and_handles_first_archive(self):
        self.write();atomic_json(self.folder/'A0012_stations_api.json',dict(data=[dict(stationId=self.sid,name='new',lon=113.,lat=23.,height=42.)]))
        old={**self.station,'name':'old','lon':114.123,'sources':['ogimet']}
        table=dict(stations=[],inactive=[old]);nmic.add_stations(table,self.root,self.at)
        self.assertEqual(table['stations'][0]['lon'],114.123);self.assertEqual(table['stations'][0]['name'],'old')
        self.assertEqual(table['stations'][0]['sources'],['ogimet','nmic']);self.assertEqual(table['inactive'],[])
        nmic.add_stations(table,self.root,self.at);self.assertEqual(len(table['stations']),1)
        table=dict(stations=[],inactive=[]);nmic.add_stations(table,self.root,self.at)
        self.assertEqual(table['stations'][0]['elevation'],42.)
        table=dict(stations=[],inactive=[]);nmic.add_stations(table,self.root,self.at+timedelta(days=46))
        self.assertEqual(len(table['inactive']),1);self.assertEqual(table['stations'],[])

    def test_province_membership_and_coordinate_regions(self):
        stations=[dict(id='new'+code,province_code=code,lon=114.1,lat=22.6,sources=['nmic']) for code in ('44','45','46','43','35')]
        with patch.object(station_api,'meta',return_value=dict(stations=stations)):
            self.assertEqual(station_api.region_stations('hn'),['new44','new45','new46'])
            self.assertEqual(len(station_api.region_stations('gba')),5)
            self.assertEqual(len(station_api.region_stations('sz')),5)

    def test_frozen_old_pairs_and_scores_exact_with_new_station(self):
        at=iso(self.at);old=[dict(id='old',type='synop')];new=[*old,self.station]
        before={('old',at,'t'):row('old',at,'t',20.,'ogimet')}
        after=select_observations([*before.values(),row('old',at,'t',30.,'nmic'),row(self.sid,at,'t',22.,'nmic')],new)
        run=self.at-timedelta(hours=3)
        a=list(pair_run('ecmwf',run,{3:dict(t=np.array([21.]))},before,old,run,self.at))
        b=list(pair_run('ecmwf',run,{3:dict(t=np.array([21.,23.]))},after,new,run,self.at))
        retained=[r for r in b if r[4]=='old'];self.assertEqual(a,retained)
        self.assertEqual(continuous([r[6] for r in a],[r[7] for r in a]),continuous([r[6] for r in retained],[r[7] for r in retained]))
        self.assertEqual(len(b),2)


class EC01(unittest.TestCase):
    def test_response_units_axis_point_order_and_one_point_shape(self):
        run=datetime(2026,11,3,tzinfo=UTC);stations=[dict(id='a',lat=23.,lon=113.)]
        data=dict(latitude=23.,longitude=113.,utc_offset_seconds=0,
                  hourly_units=dict(temperature_2m='°C',dew_point_2m='°C',pressure_msl='hPa',wind_speed_10m='m/s',precipitation='mm'),
                  hourly=dict(time=[int(run.timestamp())+3600*h for h in range(241)],**{name:[1.]*241 for name in ec.FIELDS.values()}))
        samples=ec.decode_response(data,stations,run)
        self.assertEqual(len(samples),125);self.assertEqual(samples[24]['rain_start'],23)
        self.assertEqual(accumulate({h:(v['rain_start'],v['rain']) for h,v in samples.items() if h},24,24)[0],24)
        self.assertEqual(samples[150]['rain'][0],6.);self.assertEqual(samples[150]['rain_start'],144)
        self.assertNotIn(147,samples)
        for key,value in [('utc_offset_seconds',28800),('latitude',25.)]:
            with self.assertRaises(ValueError):ec.decode_response({**data,key:value},stations,run)
        data['hourly_units']['precipitation']='inch'
        with self.assertRaises(ValueError):ec.decode_response(data,stations,run)

    def test_supplement_rain_tiled_to_grid_step_and_legacy_values_exact(self):
        base={6:dict(t=np.array([21.123456789,np.nan]),rain=np.array([9.,np.nan]),rain_start=0)}
        extra={h:dict(t=np.array([99.,22.]),rain=np.array([1.,2.]),rain_start=h-1) for h in range(1,7)}
        result=ec.merge_samples(base,extra)
        np.testing.assert_equal(result[6]['t'],[21.123456789,22.]);np.testing.assert_equal(result[6]['rain'],[9.,12.])
        self.assertEqual(result[6]['rain_start'],0)

    def test_point_cache_reorders_by_identity_and_rejects_moved_points(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'p.npz'
            save_samples(path,[3],{3:dict(t=np.array([21.123456789]))},state=dict(schema=ec.SCHEMA,points=[['a',113.,23.]]))
            rows=ec.read_points(path,[dict(id='new',lon=114.,lat=23.),dict(id='a',lon=113.,lat=23.)])
            self.assertTrue(np.isnan(rows[3]['t'][0]));self.assertEqual(rows[3]['t'][1],21.123456789)
            rows=ec.read_points(path,[dict(id='a',lon=113.1,lat=23.)])
            self.assertTrue(np.isnan(rows[3]['t'][0]))

    def test_point_only_run_enters_normal_pairer_without_regional_archive(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'forecast').mkdir();regroot=root/'regional';regroot.mkdir()
            station=dict(id='new',lon=113.,lat=23.);run=datetime(2026,11,3,tzinfo=UTC)
            path=ec.supplement_path(root,'2026110300')
            save_samples(path,[3],{3:dict(t=np.array([22.]))},state=dict(schema=ec.SCHEMA,points=[['new',113.,23.]]))
            with patch('station_forecasts.discover',return_value={}),patch('station_forecasts.regional',return_value=SimpleNamespace(root=lambda:regroot)),patch('station_forecasts.WEATHER',root/'weather'):
                runs=list(forecast_runs(root,[station],run+timedelta(hours=3)))
            self.assertEqual(len(runs),1);model,at,samples,_,_=runs[0]
            obs={('new',iso(run+timedelta(hours=3)),'t'):row('new',iso(run+timedelta(hours=3)),'t',20.,'nmic')}
            self.assertEqual(len(list(pair_run(model,at,samples,obs,[station],run,run+timedelta(hours=3)))),1)

    def test_regional_roster_expansion_keeps_old_doubles_and_extended_points(self):
        from types import SimpleNamespace
        from station_common import VERSION
        from station_forecasts import file_signature
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);out=root/'forecast';out.mkdir();directory=root/'regional/2026110300';directory.mkdir(parents=True)
            (directory/'run.json').write_text('{}')
            run=datetime(2026,11,3,tzinfo=UTC);end=run+timedelta(hours=180)
            old=dict(id='old',lon=113.,lat=23.);new=dict(id='new',lon=114.,lat=23.)
            atomic_json(root/'current.json',dict(generation='previous'))
            atomic_json(root/'snapshots/previous/meta.json',dict(stations=[old]))
            names=['temperature_2m','dew_point_2m','pressure_msl','wind_u_component_10m','wind_v_component_10m','precipitation']
            manifest=dict(leads=[0,3],fields={name:dict(sha256='0'*64) for name in names})
            manifest['fields']['precipitation'].update(unit='mm',source_times=[int((run+timedelta(hours=3)).timestamp())])
            signatures=[[name,file_signature(directory/(name+'.npy')),'0'*64] for name in names]
            oldsig=digest([['old',113.,23.]])
            file=out/'ec01_2026110300.npz';exact=21.1234567890123
            save_samples(file,[3],{3:dict(t=np.array([exact]))},state=dict(version=VERSION,station_signature=oldsig,source=digest([VERSION,oldsig,manifest,signatures])))
            save_samples(out/'ec01_2026110300.extended.npz',[180],{180:dict(t=np.array([25.123456789]))})
            reg=SimpleNamespace(root=lambda:directory.parent,manifest=lambda ident:manifest,field_file=SimpleNamespace(cache_clear=lambda:None),ec=SimpleNamespace(native_step=lambda h:3))
            def series(reg,ident,data,name,needed,target,nearest=False):return {h:np.array([22.,99.]) for h in needed}
            with patch('station_forecasts.discover',return_value={}),patch('station_forecasts.regional',return_value=reg),patch('station_forecasts.regional_series',side_effect=series),patch('station_forecasts.WEATHER',root/'weather'):
                runs=list(forecast_runs(root,[new,old],end))
            values=runs[0][2]
            self.assertEqual(values[3]['t'][1],exact);self.assertEqual(values[3]['t'][0],22.)
            self.assertEqual(values[180]['t'][1],25.123456789);self.assertTrue(np.isnan(values[180]['t'][0]))


class Equivalence(unittest.TestCase):
    def test_comparator_detects_changed_removed_and_added_old_pairs(self):
        import sqlite3
        from tools.check_nmic_equivalence import compare
        from station_score import database
        with tempfile.TemporaryDirectory() as temporary:
            roots=[Path(temporary)/n for n in ('a','b')]
            for root in roots:
                snap=root/'snapshots/generation';snap.mkdir(parents=True)
                atomic_json(root/'current.json',dict(generation='generation'))
                atomic_json(snap/'meta.json',dict(window_end='2026-11-03T00:00:00Z',stations=[dict(id='old')]))
                with database(snap/'pairs.sqlite') as db:
                    db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',('ec01','2026-11-03T00:00:00Z',0,'2026-11-03T00:00:00Z','old','t',21.,20.,'ogimet',0,'2026-11-03T00:00:00Z'))
                for days in (1,3,7,30):atomic_json(snap/f'station-scores-{days}.json',[dict(station='old',n=1,ME=1.)])
            self.assertTrue(compare(*roots)['equal'])
            path=roots[1]/'snapshots/generation/pairs.sqlite'
            with sqlite3.connect(path) as db:db.execute('UPDATE pairs SET ob=19')
            self.assertEqual(compare(*roots)['pairs']['modified'],1)
            with sqlite3.connect(path) as db:db.execute('DELETE FROM pairs')
            self.assertEqual(compare(*roots)['pairs']['removed'],1)
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO pairs VALUES ('ec01','2026-11-03T00:00:00Z',0,'2026-11-03T00:00:00Z','old','t',21,20,'ogimet',0,'2026-11-03T00:00:00Z')")
                db.execute("INSERT INTO pairs VALUES ('ec01','2026-11-03T00:00:00Z',3,'2026-11-03T03:00:00Z','old','t',21,20,'nmic',0,'2026-11-03T03:00:00Z')")
            report=compare(*roots);self.assertFalse(report['equal']);self.assertTrue(report['existing_pairs_preserved']);self.assertEqual(report['pairs']['added'],1)


if __name__ == '__main__':
    unittest.main()
