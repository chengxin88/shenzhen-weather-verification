"""CMA-GD 扩域：旧结果隔离、四模式准入、请求名单和资料整理。"""
from contextlib import redirect_stdout
import copy
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import nmic_obs
import nmic_ec01
import station_api as api
import station_forecasts as forecasts
import station_score as score
from station_common import BOX, CMAGD_BOX, CMAGD_MODELS, MODELS, UTC, atomic_json, iso
from station_obs import row, select_observations
from tools.prepare_cmagd_nmic import prepare


class CMAGDTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.folder=self.root/'raw/nmic';self.folder.mkdir(parents=True)
        self.end=datetime(2026,11,5,6,tzinfo=UTC)
        fixture=Path('tests/fixtures/nmic_sample.txt').read_text().splitlines()
        self.header=fixture[0].split();self.item=dict(zip(self.header,fixture[1].split()))
        self.core='59493';self.outer='54511'
        self.old=dict(id=self.core,type='synop',lon=114.1,lat=22.6,name='旧站',sources=['ogimet','nmic'],province_code='44')
        self.new=dict(id=self.outer,type='synop',lon=116.47,lat=39.8,name='新站',sources=['nmic'],cmagd_extended=True)
        self.env=patch.dict(os.environ,STATIONS_NMIC_DIR=str(self.folder),
                            STATIONS_NMIC_METADATA=str(self.folder/'A0012_stations_api.json'))
        self.env.start();self.addCleanup(self.env.stop)
        atomic_json(self.folder/'A0012_stations_api.json',dict(data=[
            dict(stationId=self.core,name='深圳',lon=114.1,lat=22.6,height=1),
            dict(stationId=self.outer,name='北京',lon=116.47,lat=39.8,height=32),
            dict(stationId='50136',name='框外',lon=122.52,lat=52.97,height=1)]))

    def write(self, ids):
        lines=[' '.join(self.header)]
        for sid in ids:
            item=dict(self.item,Station_Id_C=sid,Year='2026',Mon='11',Day='5',Hour='6')
            lines.append(' '.join(item[k] for k in self.header))
        (self.folder/'a.txt').write_text('\n'.join(lines)+'\n')

    def test_separate_box_unknown_metadata_diagnostics_and_repeatable_flags(self):
        self.assertEqual(BOX,(97.,18.,123.,33.))
        self.write([self.core,self.outer,'50136','99999'])
        table=dict(stations=[copy.deepcopy(self.old)])
        nmic_obs.add_stations(table,self.root,self.end)
        self.assertEqual({s['id'] for s in table['stations']},{self.core,self.outer})
        new=next(s for s in table['stations'] if s['id']==self.outer)
        self.assertTrue(new['cmagd_extended'])
        self.assertFalse(table['stations'][0]['cmagd_extended'])
        self.assertEqual(table['nmic']['missing_metadata'],['99999'])
        self.assertEqual(table['nmic']['outside_cmagd'],['50136'])
        before=copy.deepcopy(table);nmic_obs.add_stations(table,self.root,self.end)
        self.assertEqual(before,table)
        outside=dict(id='50136',type='synop',lon=122.52,lat=52.97,sources=['ogimet'])
        nmic_obs.add_stations(dict(stations=[outside]),self.root,self.end)
        self.assertEqual(outside['sources'],['ogimet'])

    def test_mixed_source_outer_keeps_original_observations_and_point_roster(self):
        self.write([self.core,self.outer])
        mixed=dict(self.new,sources=['ogimet'],cmagd_extended=False)
        table=dict(stations=[copy.deepcopy(self.old),mixed])
        nmic_obs.add_stations(table,self.root,self.end)
        self.assertFalse(mixed['cmagd_extended']);self.assertTrue(mixed['nmic_cmagd_metadata_only'])
        observations=[row(self.outer,iso(self.end),'t',20,'ogimet'),row(self.outer,iso(self.end),'td',10,'nmic')]
        before=select_observations(observations[:1],[mixed])
        self.assertEqual(before,select_observations(observations,[mixed]))
        self.assertEqual(nmic_ec01.request_stations(table['stations']),[table['stations'][0]])
        self.assertTrue(all(forecasts.model_allowed(mixed,m) for m in MODELS))

    def test_native_target_subset_precedes_sampling_and_preserves_layout(self):
        stations=[self.old,self.new]
        for model in MODELS:
            points,expand=forecasts.station_targets(stations,model)
            self.assertEqual(len(points),2 if model in CMAGD_MODELS else 1,model)
            values=expand(np.array([21.,22.] if len(points)==2 else [21.]))
            self.assertEqual(values[0],21.)
            if model not in CMAGD_MODELS:self.assertTrue(np.isnan(values[1]))

    def test_native_sampler_uses_only_eligible_coordinates(self):
        from types import SimpleNamespace
        stations=[self.old,self.new];calls=[]
        data=dict(lon=[97.,123.],lat=[18.,33.])
        def sample(path,model,run,lead,points,metadata):
            calls.append((model,points));return dict(t=np.arange(len(points),dtype=float))
        fake_native=SimpleNamespace(WN3_ERRORS=(ValueError,),WN3_SOURCES={})
        fake_reg=SimpleNamespace(root=lambda:self.root/'absent')
        groups={(m,iso(self.end-timedelta(hours=3))):{3:self.root/(m+'.json')} for m in ('gfs','ecens')}
        for m in ('gfs','ecens'):(self.root/(m+'.json')).write_text('{}')
        with patch.object(forecasts,'discover',return_value=groups),patch.object(forecasts,'native',return_value=fake_native), \
             patch.object(forecasts,'regional',return_value=fake_reg),patch.object(forecasts,'native_signature',return_value=('sig',data,[1,2])), \
             patch.object(forecasts,'read_native_frame',side_effect=sample),redirect_stdout(io.StringIO()):
            values={m:s for m,r,s,signature,issues in forecasts.forecast_runs(self.root,stations,self.end)}
        self.assertEqual(dict(calls)['gfs'],[(114.1,22.6),(116.47,39.8)])
        self.assertEqual(dict(calls)['ecens'],[(114.1,22.6)])
        self.assertTrue(np.isnan(values['ecens'][3]['t'][1]))

    def test_extended_point_certificate_remaps_without_a_previous_layout(self):
        from types import SimpleNamespace
        run=self.end.replace(hour=0);out=self.root/'forecast';out.mkdir()
        directory=self.root/'regional'/run.strftime('%Y%m%d%H');directory.mkdir(parents=True)
        (directory/'run.json').write_text('{}')
        names=['temperature_2m','dew_point_2m','pressure_msl','wind_u_component_10m','wind_v_component_10m','precipitation']
        manifest=dict(leads=[0],fields={n:dict(sha256='0'*64) for n in names})
        manifest['fields']['precipitation'].update(unit='mm',source_times=[])
        points=[[self.old['id'],self.old['lon'],self.old['lat']]]
        exact=25.1234567890123
        forecasts.save_samples(out/('ec01_'+directory.name+'.extended.npz'),[180],{180:dict(t=np.array([exact]))},
                               state=dict(version=forecasts.VERSION,station_signature=forecasts.digest(points),points=points))
        reg=SimpleNamespace(root=lambda:directory.parent,manifest=lambda ident:manifest,
                            field_file=SimpleNamespace(cache_clear=lambda:None),ec=SimpleNamespace(native_step=lambda h:3))
        def series(reg,ident,data,name,needed,target,nearest=False):return {h:np.zeros(len(target)) for h in needed}
        with patch.object(forecasts,'discover',return_value={}),patch.object(forecasts,'regional',return_value=reg), \
             patch.object(forecasts,'regional_series',side_effect=series),patch.object(forecasts,'WEATHER',self.root/'weather'),redirect_stdout(io.StringIO()):
            runs=list(forecasts.forecast_runs(self.root,[self.old,self.new],run+timedelta(hours=180)))
        self.assertEqual(runs[0][2][180]['t'][0],exact)
        self.assertTrue(np.isnan(runs[0][2][180]['t'][1]))

    def test_pair_guard_skips_forbidden_station_before_reading_forecast(self):
        class Guard:
            def __getitem__(self,i):
                if i: raise AssertionError('禁止访问新站预报')
                return 21.
        obs={(s['id'],iso(self.end),'t'):row(s['id'],iso(self.end),'t',20,'nmic') for s in (self.old,self.new)}
        for model in MODELS:
            samples={3:dict(t=np.array([21.,22.]) if model in CMAGD_MODELS else Guard())}
            pairs=list(score.pair_run(model,self.end-timedelta(hours=3),samples,obs,[self.old,self.new],self.end-timedelta(days=1),self.end))
            self.assertEqual(len(pairs),2 if model in CMAGD_MODELS else 1,model)

    def test_extended_observations_are_bounded_but_old_cutoff_is_unchanged(self):
        at=self.end-timedelta(days=20)
        records={(s['id'],at):self.item for s in (self.old,self.new)}
        rows=nmic_obs.observation_rows([self.old,self.new],self.end,self.end-timedelta(days=31),records=records)
        self.assertEqual({r['station'] for r in rows},{self.core})
        # Compact parsing retains enough pre-cutoff tiles for exact rain24.
        self.write([self.core,self.outer])
        full,_=nmic_obs.read_records(self.folder)
        expected=nmic_obs.observation_rows([self.old,self.new],self.end,self.end-timedelta(days=1),records=full)
        actual=nmic_obs.observation_rows([self.old,self.new],self.end,self.end-timedelta(days=1),root=self.root)
        self.assertEqual(expected,actual)

    def test_every_old_region_excludes_extensions_even_inside_old_box(self):
        extension=dict(self.new,lon=114.1,lat=22.6)
        for region in api.OVERVIEW_REGIONS:
            before=api.region_stations(region,[self.old])
            after=api.region_stations(region,[self.old,extension])
            self.assertEqual(after,before+[self.outer] if region=='cmagd' else before,region)
        self.assertEqual(api.REGION_BOUNDS['cmagd'],CMAGD_BOX)
        metadata=dict(window_end=iso(self.end),nmic_end=iso(self.end-timedelta(hours=3)),stations=[self.old,self.new])
        self.assertTrue(api.nmic_majority('cmagd',metadata['stations']))
        self.assertEqual(api.effective_window_end(metadata,'cmagd'),self.end-timedelta(hours=3))
        self.assertEqual(api.effective_window_end(metadata,'all'),self.end)

    def test_nmic_diagnostics_do_not_force_full_daily_rebuild(self):
        path=self.root/'stations.json';directory=self.root/'snapshots/x'
        atomic_json(path,dict(stations=[self.old],nmic=dict(records=1,latest=iso(self.end))))
        before=score.aggregate_signature(directory)
        atomic_json(path,dict(stations=[self.old],nmic=dict(records=200000,latest=iso(self.end+timedelta(hours=3)))))
        self.assertEqual(before,score.aggregate_signature(directory))
        atomic_json(path,dict(stations=[self.old,self.new]))
        self.assertNotEqual(before,score.aggregate_signature(directory))

    def test_newer_outer_reports_cannot_move_any_legacy_window(self):
        old_at=self.end-timedelta(hours=3);end=self.end+timedelta(minutes=55)
        old_obs={(self.core,iso(old_at),'t'):row(self.core,iso(old_at),'t',20.,'nmic'),
                 (self.core,iso(self.end),'t'):row(self.core,iso(self.end),'t',21.,'ogimet')}
        expanded={**old_obs,(self.outer,iso(self.end),'t'):row(self.outer,iso(self.end),'t',19.,'nmic')}
        roots=[]
        for label,stations,observed in [('window-old',[self.old],old_obs),('window-new',[self.old,self.new],expanded)]:
            root=self.root/label;root.mkdir();atomic_json(root/'stations.json',dict(stations=stations));roots.append(root)
            samples={h:dict(t=np.full(len(stations),22.)) for h in (3,6)}
            with patch.object(score,'load_observations',return_value=(observed,{},{})), \
                 patch.object(score,'forecast_runs',return_value=iter([('gfs',self.end-timedelta(hours=6),samples,'s',[])])), \
                 patch.object(score,'shenzhen_sanity',return_value=dict(n=0)),redirect_stdout(io.StringIO()):
                score.refresh(root,end)
        from tools.check_cmagd_equivalence import compare,snapshot,json_data
        with redirect_stdout(io.StringIO()):result=compare(*roots)
        self.assertTrue(result['equal'],result)
        metadata=json_data(snapshot(roots[1])/'meta.json')
        self.assertEqual(metadata['nmic_end'],iso(old_at))
        self.assertEqual(metadata['cmagd_nmic_end'],iso(self.end))
        self.assertEqual(api.effective_window_end(metadata,'hn'),old_at)
        self.assertEqual(api.effective_window_end(metadata,'cmagd'),self.end)

    def test_fast_and_sql_aggregates_preserve_old_pools(self):
        # Include rain thresholds/grades and continuous moments; each extra
        # value is large enough that a leaked new station is clearly visible.
        generated=[]
        for label,stations,legacy in [('before',[self.old],False),('after',[self.old,self.new],False),('sql',[self.old,self.new],True)]:
            root=self.root/label;root.mkdir();directory=root/'snapshots'/'20261105T060000';directory.mkdir(parents=True)
            atomic_json(root/'stations.json',dict(stations=stations))
            db=score.database(root/'working.sqlite')
            for s in stations:
                for model in CMAGD_MODELS:
                    for h in (3,24,48):
                        for v,fc,ob in [('t',21.,20.),('rain24',10.,5.)]:
                            fc+=100 if s.get('cmagd_extended') else 0
                            db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',(model,iso(self.end-timedelta(hours=h)),h,iso(self.end),s['id'],v,fc,ob,'nmic',0,iso(self.end)))
            db.commit()
            with patch.dict(os.environ,STATIONS_AGGFAST='0' if legacy else '1'),redirect_stdout(io.StringIO()):
                score.aggregates(db,directory,self.end,stations=stations)
            with sqlite3.connect(directory/'pairs.sqlite') as out: db.backup(out)
            atomic_json(directory/'common-report.json',score.common_report(db,self.end))
            db.close()
            atomic_json(directory/'meta.json',dict(window_end=iso(self.end),nmic_end=iso(self.end),stations=stations))
            atomic_json(root/'current.json',dict(generation=directory.name));generated.append(root)
        from tools.check_cmagd_equivalence import compare
        with redirect_stdout(io.StringIO()):
            result=compare(generated[0],generated[1])
        self.assertTrue(result['equal'],result)
        from tools.compare_aggregates import AggregateComparison,canonical,json_data
        for days in (1,3,7,15):
            for prefix in ('scores','station-scores'):
                c=AggregateComparison();name=f'{prefix}-{days}.json'
                c.value(canonical(json_data(generated[1]/'snapshots/20261105T060000'/name)),
                        canonical(json_data(generated[2]/'snapshots/20261105T060000'/name)),name)
                self.assertEqual(c.mismatches,0,c.errors)
        overview=api.overview_cached(str(generated[1]/'snapshots/20261105T060000'),'t',1,3,'cmagd')
        self.assertEqual({r['model'] for r in overview['rows']},set(CMAGD_MODELS))

    def test_zip_import_roundtrip_and_core_conflict_rejection(self):
        self.write([self.core,self.outer]);source=self.root/'zip';source.mkdir()
        with zipfile.ZipFile(source/'a.zip','w') as z:
            z.write(self.folder/'a.txt','../a.txt')  # never extract member paths
            z.writestr('readme.txt','说明\n')
        with zipfile.ZipFile(source/'b.zip','w') as z:z.write(self.folder/'a.txt','b.txt')
        report=prepare(source,self.folder/'A0012_stations_api.json',self.root/'prepared',self.folder)
        self.assertEqual(report['unique_reports'],2);self.assertEqual(report['duplicates'],2)
        self.assertEqual(report['baseline_conflicts'],0)
        with self.assertRaisesRegex(ValueError,'已存在'):prepare(source,self.folder/'A0012_stations_api.json',self.root/'prepared')
        data=(self.folder/'a.txt').read_text().replace(self.item['TEM'],'99999')
        with zipfile.ZipFile(source/'z.zip','w') as z:z.writestr('z.txt',data)
        with self.assertRaisesRegex(ValueError,'先核实'):prepare(source,self.folder/'A0012_stations_api.json',self.root/'bad',self.folder)


if __name__=='__main__':unittest.main()
