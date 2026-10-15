"""Corrupt-cache fixtures and bounded worker behavior; no production writes."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib,json,tempfile,time,unittest,zipfile
from unittest.mock import patch
import numpy as np
import pandas as pd
import quality_control as qc


class QualityControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.app=self.root/'app';self.weather=self.root/'weather';self.cache=self.root/'qc'
        self.app.mkdir();self.weather.mkdir();self.cache.mkdir()
        self.now=datetime(2026,10,13,6,0,tzinfo=timezone.utc)
        self.run=self.now-timedelta(hours=8)
        self.rules=qc.rules_for(self.cache)

    def tearDown(self): self.tmp.cleanup()

    def wn3(self, unit='degC', corrupt_valid=False):
        root=self.weather/'region-v4/weathernext3';root.mkdir(parents=True)
        frames=[dict(lead=h,valid=(self.run+timedelta(hours=h)).isoformat()) for h in (1,2)]
        d=dict(schema=2,model=qc.WN3,run=self.run.isoformat(),statistic='mean',native_grid_degrees=.1,
               interval_hours=1,bounds=[108.,18.,120.,27.],native_cache_directory='native-score-v1',
               forecast_horizon_hours=48,status='preparing',frames=frames)
        (root/f'run_{self.run:%Y%m%d%H}.json').write_text(json.dumps(d))
        directory=root/'native-score-v1'/f'{self.run:%Y%m%d%H}';directory.mkdir(parents=True)
        x=np.arange(1080,1201)/10;y=np.arange(180,271)/10
        for lead in (1,2):
            for field,var,u in (('surface_t','temperature_2m_mean',unit),('rain1h','total_precipitation_1hr_mean','mm')):
                meta=dict(schema='weathernext3-native-score-v1',run=self.run.isoformat(),lead=lead,
                    valid=(self.run+timedelta(hours=lead+(1 if corrupt_valid else 0))).isoformat(),
                    field=field,source_variable=var,unit=u,grid_degrees=.1,bounds=d['bounds'],statistic='mean',
                    source_kind='earth-engine-region',source_sha256='a'*64)
                a=np.full((len(y),len(x)),20 if field=='surface_t' else 0,dtype=np.float32)
                np.savez_compressed(directory/f'f{lead:03d}_{field}.npz',lon=x,lat=y,values=a,meta=json.dumps(meta))
        return root

    def native(self,slug='gfs',corrupt=False):
        root=self.weather/'region-v4'/slug;root.mkdir(parents=True)
        interval=6 if slug=='aigfs' else 3
        leads=[0,interval,interval*2]
        d=dict(schema=2,model=qc.MODELS[slug],run=self.run.isoformat(),levels=['surface'],legacy=False,
            capabilities={'surface_rh':slug!='aigfs'},partial=False,
            frames=[dict(lead=h,valid=(self.run+timedelta(hours=h)).isoformat()) for h in leads])
        (root/'catalog.json').write_text(json.dumps(d))
        for lead in leads:
            p=root/f'{slug}_{self.run:%Y%m%d%H}_f{lead:03d}_global1.json'
            meta=dict(schema=2,model=d['model'],run=d['run'],valid=(self.run+timedelta(hours=lead)).isoformat(),
                lead=lead,storage=slug+'-regional-v1',native_grid_degrees=.25,
                lon=[110.,110.25],lat=[20.,20.25],bounds=[110.,20.,110.25,20.25],levels=['surface'],
                interval_hours=interval,rain_start=(self.run+timedelta(hours=lead-interval)).isoformat())
            p.write_text(json.dumps(meta));folder=p.with_suffix('.fields');folder.mkdir()
            for field,value in {'surface_t':30.,'surface_u':1.,'surface_v':2.,'surface_msl':1000.,f'rain{interval}h':0.,'surface_rh':70.}.items():
                if slug=='aigfs' and field=='surface_rh':continue
                shape=(3,2) if corrupt and field.startswith('rain') else (2,2)
                np.save(folder/(field+'.npy'),np.full(shape,value,dtype=np.float32),allow_pickle=False)
        return root

    def test_native_corrupt_shape_rejected(self):
        self.native(corrupt=True)
        s=next(s for s in qc.native_models(self.weather,self.now,self.rules,time.monotonic()+10) if s['id']=='gfs')
        self.assertEqual(s['arrival']['available_frames'],3)
        self.assertTrue(any(i['code']=='field_invalid' and i['field']=='rain3h' for i in s['issues']))

    def test_unsupported_ai_surface_rh_not_missing(self):
        self.native('aigfs')
        s=next(s for s in qc.native_models(self.weather,self.now,self.rules,time.monotonic()+10) if s['id']=='aigfs')
        self.assertFalse(s['issues']);self.assertEqual(s['checks']['sampled_files'],10)
        self.assertFalse(any(m['field']=='surface_rh' for m in s['metrics']))

    def test_preparing_committed_hours_are_not_corrupt(self):
        self.wn3();s=qc.wn3_source(self.weather,self.now,self.rules,time.monotonic()+10)
        self.assertEqual(s['arrival']['status'],'preparing')
        self.assertEqual(s['arrival']['available_frames'],2)
        self.assertEqual(s['arrival']['expected_frames'],48)
        self.assertEqual(s['checks']['sampled_files'],4)
        self.assertFalse([i for i in s['issues'] if i['severity']=='error'])

    def test_wrong_units_and_time_are_rejected(self):
        self.wn3(unit='K',corrupt_valid=True)
        s=qc.wn3_source(self.weather,self.now,self.rules,time.monotonic()+10)
        self.assertEqual(s['checks']['sampled_files'],0)
        self.assertEqual(sum(i['code']=='field_invalid' for i in s['issues']),4)
        self.assertFalse(any(i['code']=='physical_range' for i in s['issues']))

    def test_missing_committed_file_is_error(self):
        root=self.wn3();(root/'native-score-v1'/f'{self.run:%Y%m%d%H}'/'f001_surface_t.npz').unlink()
        s=qc.wn3_source(self.weather,self.now,self.rules,time.monotonic()+10)
        self.assertTrue(any(i['code']=='field_invalid' and i['field']=='surface_t' and i['lead_hours']==1 for i in s['issues']))

    def test_backlog_48h_ready_not_a_missing_arrival(self):
        root=self.wn3()
        q=dict(runs=[dict(id=f'{self.run:%Y%m%d%H}',status='queued',target_hours=360,verification_ready=True),
                     dict(id=f'{self.run-timedelta(hours=6):%Y%m%d%H}',status='verification_ready',target_hours=48,verification_ready=True)],
               updated_utc=self.now.isoformat())
        (root/'backfill-queue.json').write_text(json.dumps(q))
        s=qc.wn3_source(self.weather,self.now,self.rules,time.monotonic()+10)
        self.assertEqual(s['arrival']['backlog']['short_pending_runs'],0)
        self.assertEqual(s['arrival']['backlog']['extended_pending_runs'],1)
        self.assertFalse(any(i['code']=='backfill_short_backlog' for i in s['issues']))

    def test_nonfinite_and_absurd_values_reported(self):
        s=qc.source('x','fixture');a=np.array([[20,800],[np.nan,np.inf]],dtype=np.float32)
        qc.values_check(s,a,'surface_t',self.run,1,self.rules)
        self.assertEqual(s['metrics'][0]['nonfinite_count'],2)
        self.assertEqual(s['metrics'][0]['range_count'],1)
        self.assertEqual({i['code'] for i in s['issues']},{'physical_range','nonfinite'})

    def test_wn2_tiny_signed_rain_tolerated(self):
        s=qc.source('x','fixture');qc.values_check(s,np.array([-.006,0.,30.]),'precipitation',self.run,6,self.rules,'mm')
        self.assertFalse(s['issues'])
        qc.values_check(s,np.array([-2.]),'precipitation',self.run,6,self.rules,'mm')
        self.assertEqual(s['issues'][0]['code'],'physical_range')

    def test_rain_review_bound_depends_on_exact_window(self):
        short=qc.source('x','hourly');long=qc.source('x','six-hour')
        qc.values_check(short,np.array([400.]),'rain1h',self.run,1,self.rules,'mm')
        qc.values_check(long,np.array([400.]),'precipitation',self.run,6,self.rules,'mm',6)
        self.assertEqual(short['metrics'][0]['range_count'],1)
        self.assertEqual(long['metrics'][0]['range_count'],0)
        self.assertEqual(short['metrics'][0]['accumulation_hours'],1)
        self.assertEqual(long['metrics'][0]['accumulation_hours'],6)

    def test_kelvin_not_tested_as_celsius(self):
        s=qc.source('x','fixture');qc.values_check(s,np.array([300.]),'temperature_2m',self.run,6,self.rules,'K')
        self.assertEqual(s['issues'][0]['code'],'unit_mismatch');self.assertFalse(s['metrics'])

    def test_sampling_is_bounded_and_endpoints_included(self):
        a=np.arange(1_000_000,dtype=np.float32).reshape(1000,1000)
        v=qc.sample_array(a)
        self.assertLessEqual(v.size,qc.CELL_LIMIT);self.assertEqual(v[0],0);self.assertEqual(v[-1],999999)

    def test_axis_duplicate_and_reverse_rejected(self):
        for x in ([110,110],[111,110]):
            with self.assertRaises(ValueError): qc.axes(x,[20,21])

    def test_frame_valid_time_and_order_checked(self):
        s=qc.source('x','fixture')
        d={'frames':[dict(lead=1,valid=(self.run+timedelta(hours=2)).isoformat()),
                     dict(lead=2,valid=(self.run+timedelta(hours=2)).isoformat()),
                     dict(lead=2,valid=(self.run+timedelta(hours=2)).isoformat())]}
        self.assertEqual(len(qc.validate_frames(s,d,self.run)),1)
        self.assertEqual(len(s['issues']),2)

    def test_observation_wind360_and_arrival_grace(self):
        root=self.app/'cache/OBSERVATION/grid';root.mkdir(parents=True)
        end=self.now.astimezone(qc.BJT)-timedelta(hours=1)
        for age in range(1,24):
            valid=end-timedelta(hours=age)
            pd.DataFrame({'GRIDID':[1,2],'T':[29.,30.],'RAIN03H':[0.,0.],'WDIR':[0.,360.],'WSPD':[0.,2.]}).to_pickle(root/f'{valid:%Y%m%d%H}.pkl')
        s=qc.observations(self.app,self.now,self.rules,time.monotonic()+10)
        self.assertEqual(s['arrival']['pending_recent_hours'],1)
        self.assertFalse(any(i['code'] in ('physical_range','observation_gaps','old_run') for i in s['issues']))
        self.assertTrue(any(m['field']=='WDIR' for m in s['metrics']))

    def test_corrupt_observation_does_not_abort_other_sources(self):
        root=self.app/'cache/OBSERVATION/grid';root.mkdir(parents=True)
        valid=self.now.astimezone(qc.BJT)-timedelta(hours=1)
        (root/f'{valid:%Y%m%d%H}.pkl').write_bytes(b'broken pickle')
        s=qc.observations(self.app,self.now,self.rules,time.monotonic()+10)
        self.assertTrue(any(i['code']=='observation_invalid' for i in s['issues']))
        self.assertFalse(s['metrics'])

    def test_npz_object_and_truncation_rejected(self):
        p=self.root/'bad.npz';np.savez(p,values=np.array([{}],dtype=object))
        with self.assertRaises(ValueError): qc.bounded_npz(p)
        p.write_bytes(b'broken')
        with self.assertRaises(zipfile.BadZipFile): qc.bounded_npz(p)

    def test_npz_oversize_is_unchecked_not_corrupt(self):
        p=self.root/'large.npz';np.savez(p,values=np.zeros((20,20)))
        with patch.object(qc,'NPZ_LIMIT',100):
            with self.assertRaises(qc.ScopeLimit): qc.bounded_npz(p)

    def test_report_does_not_write_sources_or_expose_paths(self):
        root=self.wn3();files=list(root.rglob('*'))
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
        report=qc.scan(self.app,self.weather,self.cache,self.now)
        after={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
        self.assertEqual(before,after)
        encoded=json.dumps(report,allow_nan=False)
        self.assertNotIn(str(self.root),encoded);self.assertNotIn('source_sha256',encoded)
        self.assertEqual(report['summary']['sources'],8)
        self.assertIn('demo_source',report['excluded'])

    def test_cached_snapshot_and_duplicate_refresh(self):
        report={'schema':1,'sources':[],'generated_utc':(self.now-timedelta(hours=1)).isoformat()}
        qc.atomic_json(self.cache/'snapshot.json',report)
        class FakeProcess:
            def poll(self): return None
            def kill(self): pass
        with patch.object(qc,'CACHE',self.cache),patch.object(qc,'_process',None),patch.object(qc,'_job',{}),patch.object(qc.subprocess,'Popen',return_value=FakeProcess()) as run:
            first=qc.refresh();second=qc.refresh()
            self.assertEqual(first['state'],'running');self.assertEqual(second['state'],'running')
            self.assertEqual(second['snapshot'],report);self.assertEqual(run.call_count,1)
            self.assertNotIn('_monotonic',second['job'])

    def test_ensemble_arrival_does_not_guess_horizon(self):
        run=datetime(2026,10,12,12,tzinfo=timezone.utc)
        folder=self.app/'cache/ECMWF-ENSEMBLES'/f'{run:%Y%m%d%H}'/'maps';folder.mkdir(parents=True)
        (folder/'ensemble_mean_f006_2t.npz').write_bytes(b'not numerically checked')
        s=qc.ensemble_arrival(self.app,self.now)
        self.assertEqual(s['arrival']['available_frames'],1)
        self.assertIsNone(s['arrival']['expected_frames'])
        self.assertFalse(s['metrics'])

    def checked(self,field,values,valid,window=None,in_region=True,grid=None):
        s=qc.source('fixture','seasonal fixture')
        qc.values_check(s,np.asarray(values,dtype=np.float32),field,valid,0,self.rules,
                        accumulation_hours=window,in_region=in_region,grid=grid)
        return s

    def test_seasonal_temperature_and_upper_air_separate(self):
        winter=datetime(2026,1,15,12,tzinfo=qc.BJT);summer=datetime(2026,7,15,12,tzinfo=qc.BJT)
        a=self.checked('surface_t',[45.],winter);b=self.checked('surface_t',[45.],summer)
        self.assertEqual(a['metrics'][0]['range_count'],1);self.assertEqual(b['metrics'][0]['range_count'],0)
        upper=self.checked('850_t',[-40.],summer)
        self.assertFalse(upper['issues']);self.assertNotIn('season',upper['metrics'][0])

    def test_season_uses_forecast_valid_time_in_beijing(self):
        run=datetime(2026,8,31,12,tzinfo=qc.UTC)
        s=qc.source('fixture','valid month')
        qc.values_check(s,np.array([47.]),'surface_t',run,6,self.rules,in_region=True)
        m=s['metrics'][0];self.assertEqual(m['season'],'autumn');self.assertEqual(m['valid_month_bjt'],9)
        self.assertEqual(m['review_max'],45.);self.assertEqual(m['range_count'],1)
        self.assertIn('2026-09-01T02:00',m['valid_bjt'])

    def test_seasonal_rain_uses_season_and_accumulation_window(self):
        winter=datetime(2026,1,15,12,tzinfo=qc.BJT);summer=datetime(2026,7,15,12,tzinfo=qc.BJT)
        self.assertEqual(self.checked('rain1h',[100.],winter)['metrics'][0]['range_count'],1)
        self.assertEqual(self.checked('rain1h',[100.],summer)['metrics'][0]['range_count'],0)
        six=self.checked('precipitation',[100.],winter,6)
        self.assertEqual(six['metrics'][0]['review_max'],250.);self.assertFalse(six['issues'])

    def test_cross_month_rain_uses_more_permissive_covered_month(self):
        boundary=datetime(2026,12,1,6,tzinfo=qc.BJT)
        s=self.checked('RAIN24H',[600.],boundary)
        self.assertEqual(s['metrics'][0]['review_max'],1000.);self.assertFalse(s['issues'])
        self.assertIn('跨月',s['metrics'][0]['review_context'])
        ordinary=self.checked('RAIN24H',[600.],boundary+timedelta(days=2))
        self.assertEqual(ordinary['metrics'][0]['review_max'],500.);self.assertEqual(ordinary['metrics'][0]['range_count'],1)

    def test_regional_mask_does_not_apply_southern_limits_to_northern_china(self):
        grid=(np.array([110.,151.]),np.array([25.,50.]))
        s=self.checked('surface_t',np.full((2,2),45.),datetime(2026,1,15,12,tzinfo=qc.BJT),in_region=False,grid=grid)
        metric=s['metrics'][0]
        self.assertEqual(metric['context_checked_cells'],1);self.assertEqual(metric['range_count'],1)
        self.assertEqual(metric['general_max'],70.)

    def test_unknown_location_does_not_apply_seasonal_rule(self):
        s=self.checked('surface_t',[45.],datetime(2026,1,15,12,tzinfo=qc.BJT),in_region=False)
        self.assertFalse(s['issues']);self.assertNotIn('season',s['metrics'][0])

    def test_seasonal_negative_rain_and_general_extreme_not_double_counted(self):
        valid=datetime(2026,1,15,12,tzinfo=qc.BJT)
        s=self.checked('rain1h',[-.006,0.,-2.,500.],valid)
        m=s['metrics'][0]
        self.assertEqual(m['range_count'],2);self.assertEqual(m['context_range_count'],0)
        self.assertEqual([i['code'] for i in s['issues']],['physical_range'])

    def test_wind_is_compared_as_vector_speed_at_actual_pressure_level(self):
        u=np.array([50.]);v=np.array([50.])
        low=qc.source('fixture','10m');high=qc.source('fixture','300hPa')
        qc.paired_wind_check(low,u,v,'surface',self.run,3,self.rules,87001)
        qc.paired_wind_check(high,u,v,'300',self.run,3,self.rules,87001)
        self.assertEqual(low['issues'][0]['code'],'wind_level_range');self.assertFalse(high['issues'])
        self.assertEqual(low['metrics'][0]['review_max'],60.);self.assertEqual(high['metrics'][0]['review_max'],160.)
        self.assertEqual(low['checks']['sampled_files'],0)
        self.assertTrue(low['metrics'][0]['derived']);self.assertEqual(low['metrics'][0]['total_cells'],87001)

    def test_known_upper_level_allows_jet_and_unknown_level_keeps_general_rule(self):
        high=self.checked('300_wind_speed',[155.],self.run)
        low=self.checked('850_wind_speed',[155.],self.run)
        unknown=self.checked('800_wind_speed',[155.],self.run)
        self.assertFalse(high['issues']);self.assertEqual(low['issues'][0]['code'],'wind_level_range')
        self.assertFalse(unknown['issues']);self.assertIn('高度层未知',unknown['metrics'][0]['review_context'])
        self.assertEqual(unknown['metrics'][0]['review_max'],250.)

    def test_observation_wind_speed_uses_surface_rule(self):
        s=self.checked('WSPD',[0.,61.],self.run)
        self.assertEqual(s['metrics'][0]['level'],'surface');self.assertEqual(s['metrics'][0]['range_count'],1)

    def test_native_wind_scans_declared_pressure_layers_and_missing_pair_not_healthy(self):
        root=self.native();catalog=json.loads((root/'catalog.json').read_text())
        for frame in catalog['frames']:
            meta=root/f'gfs_{self.run:%Y%m%d%H}_f{frame["lead"]:03d}_global1.json'
            d=json.loads(meta.read_text());d['levels']=['surface','300'];meta.write_text(json.dumps(d))
            for component in ('u','v'):
                np.save(meta.with_suffix('.fields')/f'300_{component}.npy',np.full((2,2),100.,dtype=np.float32))
        s=next(s for s in qc.native_models(self.weather,self.now,self.rules,time.monotonic()+10) if s['id']=='gfs')
        self.assertEqual(sum(m.get('derived',False) for m in s['metrics']),4)
        self.assertFalse(s['issues'])
        (root/f'gfs_{self.run:%Y%m%d%H}_f003_global1.fields/300_v.npy').unlink()
        s=next(s for s in qc.native_models(self.weather,self.now,self.rules,time.monotonic()+10) if s['id']=='gfs')
        self.assertTrue(any(i['code']=='field_invalid' and i.get('field')=='300_v' for i in s['issues']))
        self.assertFalse(any(m['field']=='300_wind_speed' and m['lead_hours']==3 for m in s['metrics']))

    def test_monthly_override_and_wind_configuration_are_validated(self):
        configured={'seasonal':{'profiles':{'winter':{'temperature':{'min':-15,'max':35}}},
            'monthly_overrides':{'7':{'temperature':{'min':2,'max':48},'rain_windows':{'1':180}},'13':{'temperature':{'min':0,'max':40}}}},
            'wind_levels':{'surface':55,'300':150,'850':True,'925':float('nan'),'foo':100}}
        (self.cache/'rules.json').write_text(json.dumps(configured));rules=qc.rules_for(self.cache)
        july=qc.seasonal_profile(datetime(2026,7,15,tzinfo=qc.BJT),rules)
        august=qc.seasonal_profile(datetime(2026,8,15,tzinfo=qc.BJT),rules)
        self.assertEqual(july['temperature']['max'],48);self.assertEqual(july['rain_windows']['1'],180)
        self.assertEqual(august['temperature']['max'],50);self.assertNotIn('13',rules['seasonal']['monthly_overrides'])
        self.assertEqual(rules['wind_levels']['surface'],55);self.assertEqual(rules['wind_levels']['850'],90)
        self.assertEqual(rules['wind_levels']['925'],80)

    def test_invalid_seasonal_config_falls_back_and_nonmonotonic_rain_rejected(self):
        (self.cache/'rules.json').write_text(json.dumps({'seasonal':{'profiles':{'winter':{'rain_windows':{'6':50}}},'monthly_overrides':{'7':{'rain_windows':{'6':100}}}}}))
        rules=qc.rules_for(self.cache)
        self.assertEqual(rules['seasonal']['profiles']['winter']['rain_windows']['6'],250)
        self.assertNotIn('rain_windows',rules['seasonal']['monthly_overrides']['7'])
        (self.cache/'rules.json').write_text(json.dumps({'seasonal':{'profiles':[],'monthly_overrides':None}}))
        self.assertEqual(qc.rules_for(self.cache)['wind_levels']['300'],160)

    def test_report_declares_trial_basis_profiles_and_layer_rules(self):
        self.wn3();report=qc.scan(self.app,self.weather,self.cache,self.now)
        self.assertEqual(report['rule_revision'],qc.RULE_REVISION)
        self.assertEqual(len(report['seasonal_rules']),4)
        self.assertEqual(next(r for r in report['wind_layer_rules'] if r['level']=='850')['max'],90)
        self.assertIn('试行',report['rule_policy']['basis'])
        self.assertTrue(all(m.get('valid_month_bjt')==10 for s in report['sources'] if s['id']=='weathernext3' for m in s['metrics']))

    def test_slow_native_source_budget_does_not_starve_next_model(self):
        self.native('ecmwf');self.native('gfs')
        clock=[0.];real_load=np.load
        def slow_load(*args,**kwargs):
            clock[0]+=13.;return real_load(*args,**kwargs)
        with patch.object(qc.time,'monotonic',side_effect=lambda:clock[0]),patch.object(np,'load',side_effect=slow_load):
            sources=qc.native_models(self.weather,self.now,self.rules,100.,deferred=[])
        for model in ('ecmwf','gfs'):
            s=next(s for s in sources if s['id']==model)
            self.assertEqual(s['checks']['sampled_files'],1)
            self.assertEqual(s['metrics'][0]['field'],'surface_t')
            self.assertTrue(any(i['code']=='core_scan_time_limit' for i in s['issues']))

    def test_deferred_wind_does_not_double_count_core_component(self):
        root=self.native();d=json.loads((root/'catalog.json').read_text())
        for frame in d['frames']:
            p=root/f'gfs_{self.run:%Y%m%d%H}_f{frame["lead"]:03d}_global1.json'
            meta=json.loads(p.read_text());meta['levels']=['surface','850'];p.write_text(json.dumps(meta))
            for field,value in {'850_t':0.,'850_rh':70.,'850_gh':1500.,'850_u':1.,'850_v':2.}.items():
                np.save(p.with_suffix('.fields')/(field+'.npy'),np.full((2,2),value,dtype=np.float32))
        jobs=[];sources=qc.native_models(self.weather,self.now,self.rules,time.monotonic()+30,deferred=jobs)
        s=next(s for s in sources if s['id']=='gfs');before=s['checks']['sampled_files']
        qc.deferred_pressure_winds(jobs,self.rules,time.monotonic()+30)
        self.assertEqual(s['checks']['sampled_files'],before+1)
        self.assertEqual(s['checks']['pressure_wind_layers'],['850'])
        self.assertEqual(sum(m['field']=='850_u' and m['lead_hours']==3 for m in s['metrics']),1)
        self.assertTrue(any(m['field']=='850_wind_speed' for m in s['metrics']))

    def test_deferred_wind_expired_budget_reports_unscanned_not_normal(self):
        s=qc.source('fixture','slow wind')
        jobs=[dict(source=s,folder=self.root,grid=(np.array([1.,2.]),np.array([1.,2.])),
                   run=self.run,lead=3,levels=['850','300'],sampled={},total_cells=4)]
        qc.deferred_pressure_winds(jobs,self.rules,time.monotonic()-1)
        self.assertFalse(s['metrics']);self.assertEqual(s['checks']['pressure_wind_layers'],[])
        self.assertEqual(s['issues'][0]['code'],'upper_wind_sample_scope')

    def test_native_sequential_reader_validates_header_before_allocation(self):
        p=self.root/'native.npy';np.save(p,np.ones((2,2),dtype=np.float32))
        a=qc.native_array(p,(2,2));self.assertEqual(a.shape,(2,2));self.assertNotIsInstance(a,np.memmap)
        with self.assertRaises(ValueError):qc.native_array(p,(3,2))
        raw=p.read_bytes();p.write_bytes(raw[:-1])
        with self.assertRaises(ValueError):qc.native_array(p,(2,2))
        np.save(p,np.ones((2,2),dtype=np.float64))
        with self.assertRaises(ValueError):qc.native_array(p,(2,2))


if __name__=='__main__': unittest.main()
