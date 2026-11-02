"""Exact valid-time diffs, transactional recovery and publication invariants."""
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import station_score as score
from station_common import UTC, atomic_json, digest, iso
from station_forecasts import SamplePlan, load_samples, save_samples
from station_obs import row


class PairIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.db = score.database(self.root/'working.sqlite')
        self.addCleanup(self.db.close)
        self.run = datetime(2026,10,25,tzinfo=UTC); self.end = self.run+timedelta(hours=9)
        self.cutoff = self.run-timedelta(days=31)
        self.stations = [dict(id='a')]
        self.samples = {h:dict(t=np.array([20.+h])) for h in (3,6,9)}
        self.obs = {('a',iso(self.run+timedelta(hours=h)),'t'):row('a',iso(self.run+timedelta(hours=h)),'t',20.,'ogimet') for h in (3,6)}

    def tick(self, source='s', samples=None):
        signatures = {key[1]:digest(value) for key,value in self.obs.items()}
        return score.score_job(self.db,'ec01',self.run,self.samples if samples is None else samples,
                               source,signatures,self.obs,self.stations,self.cutoff,self.end)

    def rows(self):
        return self.db.execute('SELECT * FROM pairs ORDER BY lead').fetchall()

    def test_new_revised_removed_observations_only_replace_their_valids(self):
        self.assertEqual(self.tick()[:3],(True,2,True))
        untouched = self.rows()[0]
        at = iso(self.end); self.obs['a',at,'t'] = row('a',at,'t',19.,'ogimet')
        self.assertEqual(self.tick(),(True,1,False,1))
        at = iso(self.run+timedelta(hours=6)); self.obs['a',at,'t']['value'] = 18.
        self.assertEqual(self.tick(),(True,1,False,1))
        del self.obs['a',at,'t']
        self.assertEqual(self.tick(),(True,0,False,1))
        self.assertEqual([r[2] for r in self.rows()],[3,9]); self.assertEqual(self.rows()[0],untouched)
        self.assertFalse(self.tick()[0])

    def test_unchanged_tick_makes_no_database_writes(self):
        self.tick();before=self.db.total_changes
        self.assertEqual(self.tick(),(False,0,False,0))
        self.assertEqual(self.db.total_changes,before)
        self.assertFalse(self.db.in_transaction)

    def test_source_change_new_run_and_version_force_full_pairing(self):
        self.tick(); self.assertTrue(self.tick('changed')[2])
        with patch.object(score,'VERSION','unit-version-bump'):
            self.assertTrue(self.tick('changed')[2])
        self.run += timedelta(hours=3); self.end += timedelta(hours=3)
        self.assertTrue(self.tick('changed')[2])

    def test_legacy_signature_migrates_without_loading_unchanged_samples(self):
        relevant = sorted((iso(self.run+timedelta(hours=h)),None) for h in self.samples)
        self.obs.clear()
        self.db.execute('INSERT INTO jobs VALUES (?,?,?)',('ec01',iso(self.run),digest([score.VERSION,'s',relevant])))
        self.db.commit()
        def fail(_):raise AssertionError('unchanged run loaded samples')
        samples=SamplePlan(self.samples,'s',fail)
        self.assertEqual(self.tick(samples=samples),(False,0,False,0))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM job_valid').fetchone()[0],3)

    def test_rolloff_is_not_an_observation_change(self):
        self.tick(); self.cutoff = self.run+timedelta(hours=5)
        self.db.execute('DELETE FROM pairs WHERE valid<?',(iso(self.cutoff),));self.db.commit()
        self.assertFalse(self.tick()[0]);self.assertEqual([r[2] for r in self.rows()],[6])
        self.assertEqual(score.valid_diff({'a':'old','b':None},{'b':None,'c':'new'},'b','z'),{'c'})

    def test_end_advance_pairs_only_the_new_valid_and_loads_rain_dependencies(self):
        self.end = self.run+timedelta(hours=6);self.tick()
        self.end += timedelta(hours=3);at=iso(self.end)
        self.obs['a',at,'t'] = row('a',at,'t',20.,'ogimet')
        requested=[]
        samples=SamplePlan(self.samples,'s',lambda needed:requested.append(needed) or self.samples)
        self.assertEqual(self.tick(samples=samples),(True,1,False,1))
        self.assertEqual(requested,[{3,6,9}])

    def test_transaction_error_preserves_pairs_digests_and_dirty_days(self):
        self.tick();before=self.rows()
        self.db.execute('DELETE FROM dirty_days');self.db.commit()
        self.db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON pairs BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.obs['a',iso(self.run+timedelta(hours=6)),'t']['value']=17.
        with self.assertRaises(sqlite3.IntegrityError):self.tick()
        self.assertEqual(self.rows(),before)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dirty_days').fetchone()[0],0)
        self.db.execute('DROP TRIGGER fail_insert');self.db.commit()
        self.assertEqual(self.tick(),(True,1,False,1))

    def test_sigkill_rolls_back_uncommitted_pair_transaction(self):
        self.tick();before=self.rows()
        script="""import os,signal,sqlite3,sys
con=sqlite3.connect(sys.argv[1]);con.execute('DELETE FROM pairs')
con.execute('DELETE FROM job_valid');con.execute('DELETE FROM dirty_days')
os.kill(os.getpid(),signal.SIGKILL)
"""
        child=subprocess.run([sys.executable,'-c',script,str(self.root/'working.sqlite')])
        self.assertEqual(child.returncode,-signal.SIGKILL)
        self.assertEqual(self.rows(),before);self.assertFalse(self.tick()[0])
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_legacy_precision_upgrade_keeps_pairs_exact_and_is_repeatable(self):
        value=21.1234567890123;at=iso(self.run+timedelta(hours=3));path=self.root/'legacy.npz'
        np.savez_compressed(path,leads=np.array([3]),t=np.array([[value]],dtype=np.float32))
        atomic_json(path.with_suffix('.sample-state.json'),dict(station_signature='frozen',sources=[[3,'s']],sample_file_signature=[path.stat().st_mtime_ns,path.stat().st_size]))
        self.db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',('ec01',iso(self.run),3,at,'a','t',value,20.,'ogimet',0,at));self.db.commit()
        samples=SamplePlan([3],'s',lambda needed:load_samples(path,needed),path)
        before=self.rows();score.upgrade_pair_precision(self.db,'ec01',iso(self.run),samples,self.stations)
        self.assertEqual(self.rows(),before);self.assertEqual(load_samples(path)[3]['t'][0],value)
        mtime=path.stat().st_mtime_ns
        score.upgrade_pair_precision(self.db,'ec01',iso(self.run),samples,self.stations)
        self.assertEqual(path.stat().st_mtime_ns,mtime)

    def test_precision_upgrade_rejects_a_real_forecast_difference(self):
        path=self.root/'legacy.npz';np.savez_compressed(path,leads=np.array([3]),t=np.array([[21.]],dtype=np.float32))
        atomic_json(path.with_suffix('.sample-state.json'),dict(station_signature='frozen',sources=[[3,'s']],sample_file_signature=[path.stat().st_mtime_ns,path.stat().st_size]))
        at=iso(self.run+timedelta(hours=3))
        self.db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',('ec01',iso(self.run),3,at,'a','t',22.,20.,'ogimet',0,at));self.db.commit()
        samples=SamplePlan([3],'s',lambda needed:load_samples(path,needed),path)
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'beyond float32 rounding'):
            score.upgrade_pair_precision(self.db,'ec01',iso(self.run),samples,self.stations)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.rows()[0][6],22.)

    def test_new_sample_cache_does_not_round_fresh_values_to_float32(self):
        value = np.array([21.1234567890123])
        path=self.root/'samples.npz';save_samples(path,[3],{3:{'t':value}})
        self.assertEqual(load_samples(path)[3]['t'][0],value[0])


class PublicationRecoveryTests(unittest.TestCase):
    def test_pruning_protects_current_even_after_rollback_and_ignores_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);snapshots=root/'snapshots';snapshots.mkdir()
            for name in ('20261025T000000','20261026T000000','20261027T000000'):
                directory=snapshots/name;directory.mkdir()
                (directory/'pairs.sqlite').touch();atomic_json(directory/'meta.json',{})
            for name in ('20261028T000000','.incomplete-20261029T000000'):(snapshots/name).mkdir()
            atomic_json(root/'current.json',dict(generation='20261025T000000'))
            score.prune_snapshots(root)
            self.assertEqual(sorted(p.name for p in snapshots.iterdir()),['20261025T000000','20261027T000000'])

    def test_killed_publish_keeps_current_and_exports_pending_on_retry(self):
        station=dict(id='a',type='synop',name='test',lat=23.,lon=113.,elevation=10,sources=['ogimet'])
        end=datetime(2026,10,25,6,tzinfo=UTC);run=end-timedelta(hours=6);at=iso(end)
        observations={('a',at,'t'):row('a',at,'t',20.,'ogimet')}
        source=[('ec01',run,{6:{'t':np.array([21.])}},'s',[])]
        with tempfile.TemporaryDirectory() as temporary,patch.object(score,'forecast_runs',side_effect=lambda *a:iter(source)), \
             patch.object(score,'load_observations',side_effect=lambda *a:(observations,{},{})), \
             patch.object(score,'shenzhen_sanity',return_value={'n':0}):
            root=Path(temporary);atomic_json(root/'stations.json',dict(stations=[station]))
            score.refresh(root,end);current=(root/'current.json').read_bytes()
            observations['a',at,'t']['value']=19.
            with patch.object(score,'export_days',side_effect=RuntimeError('killed before export')):
                with self.assertRaises(RuntimeError):score.refresh(root,end)
            self.assertEqual((root/'current.json').read_bytes(),current)
            with sqlite3.connect(root/'working.sqlite') as db:
                self.assertEqual(db.execute('SELECT day FROM dirty_days').fetchall(),[(at[:10],)])
            result=score.refresh(root,end)
            self.assertEqual(result['processed_runs'],0)
            self.assertFalse(any(p.name.startswith('.incomplete-') for p in (root/'snapshots').iterdir()))
            import gzip
            with gzip.open(root/'pairs'/(at[:10]+'.csv.gz'),'rt') as handle:self.assertIn('19.0',handle.read())
            with sqlite3.connect(root/'working.sqlite') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM dirty_days').fetchone()[0],0)



class AggregateEquivalenceTests(unittest.TestCase):
    def test_single_pass_preserves_pooled_cc_and_every_contingency_threshold(self):
        import math
        from station_common import VARIABLES, THRESHOLDS
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);db=score.database(root/'working.sqlite');self.addCleanup(db.close)
            end=datetime(2026,10,25,9,17,tzinfo=UTC)
            rng=np.random.default_rng(1234)
            records=[]
            for age in (0,1,3,7,29,30,31):
                for offset in (-1,0,1):
                    valid=end-timedelta(days=age)+timedelta(seconds=offset)
                    for model in ('ec01','gfs'):
                        for station in ('a','b'):
                            for lead in (3,27,81):
                                for var in VARIABLES:
                                    fc,ob=rng.uniform(0,25,2)
                                    if var=='msl':fc+=1000;ob+=1000
                                    run=valid-timedelta(hours=lead)
                                    records.append((model,iso(run),lead,iso(valid),station,var,fc,ob,'ogimet',0,iso(valid)))
            db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',records);db.commit()
            with patch.object(score,'grid_aggregates'):
                score.aggregates(db,root,end)
            for days in (1,3,7,30):
                expected=[];params=(iso(end-timedelta(days=days)),iso(end))
                for var in VARIABLES:
                    for threshold in THRESHOLDS.get(var,[None]):
                        expected+=score.stats_sql(db,['model','var','lead'],'valid>=? AND valid<=? AND var=?',(*params,var),var in THRESHOLDS,threshold)
                actual=json.loads((root/f'scores-{days}.json').read_text())
                self.assertEqual(len(actual),len(expected))
                for left,right in zip(actual,expected):
                    self.assertEqual(left.keys(),right.keys())
                    for key in left:
                        if isinstance(left[key],float):
                            self.assertTrue(math.isclose(left[key],right[key],rel_tol=1e-9,abs_tol=1e-12),(days,key,left,right))
                        else:self.assertEqual(left[key],right[key])



class SourceCacheTests(unittest.TestCase):
    def test_negative_field_cache_detects_addition_and_in_place_revision(self):
        import station_forecasts as forecasts
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'ecmwf_2026102500_f003_global1.json'
            path.write_text('{}');fields=path.with_suffix('.fields');fields.mkdir()
            field=fields/'surface_t.npy';np.save(field,np.array([20.],dtype=np.float32))
            saved=dict(path=str(path),meta_stat=forecasts.file_signature(path),metadata={})
            with patch.object(forecasts.time,'time_ns',return_value=fields.stat().st_ctime_ns+10**10):
                first=forecasts.native_signature(path,saved)[0]
            with patch.object(forecasts,'file_signature',wraps=forecasts.file_signature) as stat:
                self.assertEqual(forecasts.native_signature(path,saved)[0],first)
                checked=[call.args[0] for call in stat.call_args_list]
                self.assertIn(field,checked);self.assertNotIn(fields/'orography.npy',checked)
            before_dir=fields.stat().st_mtime_ns
            np.save(fields/'orography.npy',np.array([1.],dtype=np.float32))
            os.utime(fields,ns=(before_dir+10**10,before_dir+10**10))
            second=forecasts.native_signature(path,saved)[0]
            self.assertNotEqual(first,second)
            before=field.stat().st_mtime_ns;np.save(field,np.array([21.],dtype=np.float32))
            os.utime(field,ns=(before+10**9,before+10**9))
            self.assertNotEqual(forecasts.native_signature(path,saved)[0],second)

    def test_recent_directory_edits_do_not_reuse_negative_results(self):
        import station_forecasts as forecasts
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'ecmwf_2026102500_f003_global1.json';path.write_text('{}')
            fields=path.with_suffix('.fields');fields.mkdir()
            saved=dict(path=str(path),meta_stat=forecasts.file_signature(path),metadata={})
            first=forecasts.native_signature(path,saved)[0]
            np.save(fields/'orography.npy',np.array([1.],dtype=np.float32))
            self.assertNotEqual(forecasts.native_signature(path,saved)[0],first)

    def test_embedded_certificate_survives_stale_sidecars(self):
        from station_forecasts import sample_state
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'samples.npz';sidecar=root/'state.json'
            atomic_json(sidecar,dict(station_signature='old',sources=[]))
            state=dict(station_signature='new',sources=[[3,'source']])
            save_samples(path,[3],{3:dict(t=np.array([21.123456789]))},state=state)
            loaded,embedded=sample_state(path,sidecar)
            self.assertTrue(embedded);self.assertEqual(loaded,state)
            with np.load(path,allow_pickle=False) as archive:
                self.assertEqual(set(archive.files),{'leads','t'})
            self.assertEqual(load_samples(path)[3]['t'][0],21.123456789)



class SanityCacheTests(unittest.TestCase):
    def test_grid_cache_reuses_unchanged_sources_but_recomputes_observation_scores(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'data').mkdir();(root/'data/grid.xlsx').touch()
            grid_dir=root/'cache/OBSERVATION/grid';grid_dir.mkdir(parents=True)
            grid_file=grid_dir/'2026102508.pkl';grid_file.touch()
            atomic_json(root/'stations.json',dict(stations=[dict(id='59493',lon=113.,lat=23.,coordinate_source='frozen')]))
            at='2026-10-25T00:00:00Z';obs={('59493',at,'t'):dict(value=21.)}
            grid=pd.DataFrame({'格网左下角经度（度）':[112.5],'格网右上角经度（度）':[113.5],
                               '格网左下角纬度（度）':[22.5],'格网右上角纬度（度）':[23.5],'格网ID（唯一）':[7]})
            with patch.dict(os.environ,STATIONS_GRID_ROOT=str(root)), \
                 patch.object(pd,'read_excel',return_value=grid) as excel, \
                 patch.object(pd,'read_pickle',return_value=pd.DataFrame(dict(GRIDID=[7],T=[20.5]))) as read:
                self.assertEqual(score.shenzhen_sanity(obs,root)['ME'],.5)
                obs['59493',at,'t']['value']=22.
                self.assertEqual(score.shenzhen_sanity(obs,root)['ME'],1.5)
                self.assertEqual(read.call_count,1);self.assertEqual(excel.call_count,1)
                before=grid_file.stat().st_mtime_ns;os.utime(grid_file,ns=(before+10**9,before+10**9))
                read.return_value=pd.DataFrame(dict(GRIDID=[7],T=[21.]))
                self.assertEqual(score.shenzhen_sanity(obs,root)['ME'],1.)
                self.assertEqual(read.call_count,2);self.assertEqual(excel.call_count,1)



class ComparisonToolTests(unittest.TestCase):
    def test_key_merge_reports_added_and_modified_rows_without_cascading(self):
        from tools.compare_station_runs import Comparison
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name,rows in [('a',[('a',1.),('b',2.)]),('b',[('a',1.),('b',3.),('c',4.)])]:
                db=sqlite3.connect(root/(name+'.sqlite'))
                db.execute('CREATE TABLE pairs (model TEXT PRIMARY KEY,fc REAL)')
                db.executemany('INSERT INTO pairs VALUES (?,?)',rows);db.commit();db.close()
            result=Comparison();result.sqlite(root/'a.sqlite',root/'b.sqlite','pairs','model',exact=True)
            self.assertEqual(result.checked['pairs'],dict(a_rows=2,b_rows=3,matched=2,added=1,removed=0,modified=1))
            self.assertEqual(result.mismatches,2)



class RainPrecisionTests(unittest.TestCase):
    def test_exact_six_and_twentyfour_hour_totals_with_mixed_interval_precision(self):
        from station_precision import restore_rain
        raw={h:np.array([v,2*v]) for h,v in zip(range(3,25,3),(.001,.02,.003,.04,.005,.06,.007,.08))}
        samples={h:dict(rain=v.astype(np.float32).astype(float),rain_start=h-3) for h,v in raw.items()}
        original={h:(h-3,v if h in (3,12,15,18) else v.astype(np.float32)) for h,v in raw.items()}
        rows=[]
        from station_forecasts import accumulate
        for lead,hours in [(6,6),(12,6),(18,6),(24,6),(24,24)]:
            fc=accumulate(original,lead,hours)
            rows.extend((lead,sid,'rain'+str(hours),float(fc[i])) for i,sid in enumerate(('a','b')))
        sources=[(h,[f'/archive/ecmwf_2026102500_f{h:03}.json',[1,1],[]]) for h in raw]
        count=restore_rain(samples,sources,{},'ecmwf','2026-10-25T00:00:00Z',[dict(id='a'),dict(id='b')],rows)
        self.assertGreater(count,0)
        intervals={h:(v['rain_start'],v['rain']) for h,v in samples.items()}
        for lead,sid,var,fc in rows:
            self.assertEqual(float(accumulate(intervals,lead,int(var[4:]))[('a','b').index(sid)]),fc)

    def test_original_metadata_restores_non_decimal_interval_values(self):
        from station_precision import restore_rain
        from station_forecasts import accumulate
        run='2026-10-25T00:00:00Z';raw={3:.00012345000017,6:.33312345000014}
        samples={h:dict(rain=np.array([v],dtype=np.float32),rain_start=h-3) for h,v in raw.items()}
        sources=[];metadata={}
        for h,value in raw.items():
            path=f'/archive/ecmwf_2026102500_f{h:03}.json';sources.append((h,[path,[1,1],[]]))
            data=dict(schema=2,model='ECMWF-IFS',run=run,valid=iso(datetime(2026,10,25,tzinfo=UTC)+timedelta(hours=h)),lead=h,
                      lon=[108.,108.25],lat=[18.,18.25],rain3h=[value]*4,
                      rain_start=iso(datetime(2026,10,25,tzinfo=UTC)+timedelta(hours=h-3)))
            metadata[str(h)]=dict(path=path,meta_stat=[1,1],metadata=data)
        expected=raw[6]+raw[3]
        restore_rain(samples,sources,metadata,'ecmwf',run,[dict(id='a',lon=108.1,lat=18.1)],[(6,'a','rain6',expected)])
        self.assertEqual(float(accumulate({h:(s['rain_start'],s['rain']) for h,s in samples.items()},6,6)[0]),expected)

    def test_inconsistent_totals_fail_without_changing_intervals(self):
        from station_precision import restore_rain
        samples={3:dict(rain=np.array([.01],dtype=np.float32),rain_start=0),6:dict(rain=np.array([.02],dtype=np.float32),rain_start=3)}
        before={h:s['rain'].copy() for h,s in samples.items()}
        sources=[(h,[f'/archive/ecmwf_2026102500_f{h:03}.json',[1,1],[]]) for h in samples]
        with self.assertRaisesRegex(ValueError,'cannot reproduce'):
            restore_rain(samples,sources,{},'ecmwf','2026-10-25T00:00:00Z',[dict(id='a')],[(6,'a','rain6',999.)])
        for h in samples:np.testing.assert_equal(samples[h]['rain'],before[h])

    def test_upgrade_pair_precision_migrates_legacy_rain_and_leaves_exact_untouched(self):
        from station_forecasts import accumulate, sample_state
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);db=score.database(root/'working.sqlite');self.addCleanup(db.close)
            run=datetime(2026,10,25,tzinfo=UTC);at=iso(run+timedelta(hours=6))
            raw3,raw6,raw9=np.array([.001]),np.array([.03]),np.array([.125])
            f32_3,f32_6,f32_9=raw3.astype(np.float32).astype(float),raw6.astype(np.float32).astype(float),raw9.astype(np.float32).astype(float)
            path=root/'ecmwf_2026102500.npz'
            sources=[[h,[f'/archive/ecmwf_2026102500_f{h:03}.json',[1,1],[]]] for h in (3,6,9)]
            state=dict(station_signature='frozen',sources=sources,pair_precision=1)
            save_samples(path,[3,6,9],{3:dict(rain=f32_3,rain_start=0.),6:dict(rain=f32_6,rain_start=3.),9:dict(rain=f32_9,rain_start=6.)},state=state)
            atomic_json(path.with_suffix('.sample-state.json'),dict(state,sample_file_signature=score.file_signature(path)))
            expected6=float((raw3+raw6)[0]);exact9=float((raw6+f32_9)[0])
            db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',[
                ('ecmwf',iso(run),6,at,'a','rain6',expected6,0.,'ogimet',0,at),
                ('ecmwf',iso(run),9,iso(run+timedelta(hours=9)),'a','rain6',exact9,0.,'ogimet',0,iso(run+timedelta(hours=9)))])
            db.commit()
            samples=SamplePlan([3,6,9],'s',lambda needed:load_samples(path,needed),path)
            score.upgrade_pair_precision(db,'ecmwf',iso(run),samples,[dict(id='a')])
            upgraded=load_samples(path)
            intervals={h:(s['rain_start'],s['rain']) for h,s in upgraded.items()}
            self.assertEqual(float(accumulate(intervals,6,6)[0]),expected6)
            self.assertEqual(float(accumulate(intervals,9,6)[0]),exact9)
            self.assertEqual(float(upgraded[9]['rain'][0]),float(f32_9[0]))
            self.assertEqual(sample_state(path,path.with_suffix('.sample-state.json'))[0].get('pair_precision'),2)
            mtime=path.stat().st_mtime_ns
            score.upgrade_pair_precision(db,'ecmwf',iso(run),samples,[dict(id='a')])
            self.assertEqual(path.stat().st_mtime_ns,mtime)



if __name__=='__main__':unittest.main()
