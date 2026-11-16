"""Hand-counted definitions, producer compatibility, migration, and independent API paths."""
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
import numpy as np
import pandas as pd
import guifan_scores as g
import station_score as st
import station_api as api
from station_common import UTC, iso, atomic_json, bjt, GRID_MODELS, THRESHOLDS
from station_obs import derive_rain12


class Definitions(unittest.TestCase):
    def test_exact_temperature_and_tolerance(self):
        # 1, 2, both sides of 2 within tolerance, then outside tolerance.
        self.assertEqual(g.accuracy([1.,2.,1.9999999,2.0000001,2.000002],[0]*5),dict(N1=1,N2=4))
        self.assertEqual(g.accuracy([20.1,20.1],[19.1,18.1]),dict(N1=1,N2=2))

    def test_trace_grid_and_station(self):
        fc=[.1,.1,0,0,.1,0,.1,0]
        ob=[.1,.05,.05,0,0,.1,0,.1]
        row=g.rain_rows({},fc,ob,3)[0]
        self.assertEqual((row['n'],row['H'],row['M'],row['F'],row['T']),(8,1,2,3,1))
        self.assertEqual(g.derived(row)['PC'],.5)
        # Trace=1 at zero counts; unflagged .05 is not a station trace;
        # a trace flag attached to measurable .1 must never count twice.
        row=g.rain_rows({},[.1]*4,[0,.05,.1,0],24,[True,False,True,False])[0]
        self.assertEqual((row['H'],row['F'],row['T']),(1,3,1))
        self.assertEqual(g.derived(row)['PC'],.5)

    def test_every_7_by_7_cell_and_boundaries(self):
        for hours,values in [(3,[0,.1,3,10,20,50,70]),(24,[0,.1,10,25,50,100,250])]:
            for o in range(7):
                for f in range(7):
                    rows=[r for r in g.rain_rows({},[values[f]],[values[o]],hours) if r.get('grade')]
                    for k,row in enumerate(rows,1):
                        expected=(int(f==o==k),int(f<o and o==k),int(f>o and f==k))
                        self.assertEqual((row['H'],row['M'],row['F']),expected,(hours,o,f,k))
                    self.assertEqual(sum(r[c] for r in rows for c in ('H','M','F')),int(f>0 or o>0))
        for hours,amount,grade in [(3,.099999,0),(3,.1,1),(3,2.95,1),(3,3.,2),(24,9.99,1),(24,250,6)]:
            rows=g.rain_rows({},[amount],[amount],hours)
            self.assertEqual([r['grade'] for r in rows if r.get('grade') and r['H']],[] if not grade else [grade])

    def test_rain12_trace_union_only_for_sub_measurable_total(self):
        for first,second in [(True,False),(False,True),(True,True),(False,False)]:
            for total in (0,.05,.1):
                rows={('a','2026-10-01T00:00:00Z','rain6'):dict(value=total,trace=first),
                      ('a','2026-10-01T06:00:00Z','rain6'):dict(value=0,trace=second)}
                derive_rain12(rows)
                self.assertEqual(rows['a','2026-10-01T06:00:00Z','rain12']['trace'],total<.1 and (first or second))


class StationProducts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.directory=self.root/'snapshots'/'20261025T000000';self.directory.mkdir(parents=True)
        self.db=st.database(self.root/'working.sqlite');self.addCleanup(self.db.close)
        self.end=datetime(2026,10,25,0,tzinfo=UTC);self.valid=iso(self.end);self.run=iso(self.end-timedelta(hours=24))
        self.site=dict(id='a',type='synop',sources=['ogimet'],lon=114.,lat=22.5,elevation=0)
        records=[]
        for i,(f,o,t) in enumerate([(0,0,0),(.1,0,1),(10,1,0),(1,10,0),(250,250,0)]):
            records.append(('ec01',self.run,24,self.valid,'a'+str(i),'rain24',f,o,'ogimet',t,self.valid))
            records.append(('ec01',self.run,24,self.valid,'a'+str(i),'t',o+([1,2,2.0000001,3,0][i]),o,'ogimet',0,self.valid))
        for variable in ('td','msl','wind','rain6','rain12'):
            for i,(f,o,t) in enumerate([(0.,0.,0),(.1,0.,1),(10.,1.,0),(1.,10.,0),(250.,250.,0)]):
                if variable=='msl':f,o=1000.+i*1e-7+1e-8,1000.+i*2e-7
                records.append(('ec01',self.run,24,self.valid,'a'+str(i),variable,f,o,'ogimet',t,self.valid))
        self.db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',records);self.db.commit()
        st.aggregates(self.db,self.directory,self.end)
        self.db.backup(sqlite3.connect(self.directory/'pairs.sqlite'))
        meta=dict(stations=[dict(self.site,id='a'+str(i)) for i in range(5)],window_end=self.valid,generated=self.valid,
                  variables=[dict(id='t',has_acc=True),dict(id='rain24',**g.metadata(hours=24))],models=[dict(id='ec01',leads=[24])])
        atomic_json(self.directory/'meta.json',meta);atomic_json(self.root/'current.json',dict(generation=self.directory.name))
        self.env=patch.dict('os.environ',STATIONS_CACHE_DIR=str(self.root));self.env.start();self.addCleanup(self.env.stop);api.cached_json.cache_clear()

    def test_grid_daily_detail_and_maps_match_hand_counts(self):
        with gzip.open(self.directory/'grid-rows-t.json.gz','rt') as h:row=json.load(h)[0]
        self.assertEqual((row['N1'],row['N2'],row['n']),(2,4,5))
        with gzip.open(self.directory/'grid-rows-rain24.json.gz','rt') as h:rows=json.load(h)
        first=next(r for r in rows if r.get('threshold')==.1)
        self.assertEqual((first['H'],first['M'],first['F'],first['T'],first['GO'],first['GU']),(3,0,1,1,1,1))
        self.assertEqual(g.derived(first)['PC'],1.)
        self.assertEqual(len([r for r in rows if r.get('grade')]),6)
        params=dict(model='ec01',var='rain24',start='2026-10-25',end='2026-10-25',leads='24')
        all_hours=api.map_scores(params)['rows']
        selected_hour=api.map_scores(dict(params,hour='08'))['rows']
        self.assertEqual(all_hours,selected_hour)
        self.assertEqual(sum(r['T'] for r in all_hours),1)
        graded=api.map_scores(dict(params,grade='2'))['rows']
        self.assertTrue(all('ETS' in r for r in graded))
        for r in graded:
            h,m,f,n=r['H'],r['M'],r['F'],r['n']
            chance=(h+m)*(h+f)/n
            expected=(h-chance)/(h+m+f-chance) if h+m+f-chance else None
            self.assertEqual(r['ETS'],expected)
        self.assertEqual(sum(r['F'] for r in graded),1)
        for hour in ('all','08'):
            detail=api.station(dict(id='a1',var='rain24',model='ec01',group='near',days='15',hour=hour))
            self.assertEqual(next(r for r in detail['stats'] if r.get('threshold')==.1)['PC'],1.)
        points=api.grid_rows(dict(dataset='station-气温-2m',station='a2',start='2026-10-25',end='2026-10-25'))
        self.assertEqual((points[0]['N1'],points[0]['N2']),(0,1))

    def test_trace_migration_preserves_pairs_and_dirty_days(self):
        before=self.db.execute('SELECT fc,ob FROM pairs ORDER BY model,run,lead,station,var').fetchall()
        observations={('a1',self.valid,'rain24'):dict(value=0,source='ogimet',trace=False)}
        st.migrate_trace(self.db,observations)
        self.assertEqual(self.db.execute('SELECT trace FROM pairs WHERE station=? AND var=?',('a1','rain24')).fetchone()[0],0)
        self.assertEqual(before,self.db.execute('SELECT fc,ob FROM pairs ORDER BY model,run,lead,station,var').fetchall())
        self.assertEqual(self.db.execute('SELECT day FROM dirty_days').fetchall(),[('2026-10-25',)])
        st.migrate_trace(self.db,{})

    def test_native_original_scores_are_exact(self):
        import native_archive as native
        d=dict(run=self.run,valid=self.valid,model='ECMWF-IFS',lead=24,rain3h=[1],rain_start=iso(self.end-timedelta(hours=3)),layers=dict(surface=dict(t=[1])))
        f=np.array([1.,2.,2.0000001,3.]);o=np.zeros(4)
        with patch.object(native,'paired',return_value=(f,o)):
            rows=native.score_frame(d,None,None,1)
        temp=next(r for r in rows if r['dataset']=='hk-气温-2m')
        self.assertEqual((temp['N1'],temp['N2']),(1,3))
        self.assertEqual((temp['ME'],temp['MAE'],temp['RMSE']),(float((f-o).mean()),float(abs(f-o).mean()),float(np.sqrt(((f-o)**2).mean()))))
        for r in rows:
            if r.get('threshold') is not None:
                expected=st.contingency(f,o,r['threshold'])
                self.assertEqual([r[k] for k in ('H','M','F','n')],[expected[k] for k in ('H','M','F','n')])


    def test_checker_read_only_and_detects_changed_metric_or_counter(self):
        from tools.check_guifan import main
        import contextlib,io,hashlib
        files=[p for p in self.directory.iterdir() if p.is_file()]
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.directory),str(self.directory),'--sample','10']),0)
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
        with gzip.open(self.directory/'grid-rows-t.json.gz','rt') as h:rows=json.load(h)
        old=self.root/'old.json';new=self.root/'new.json';atomic_json(old,dict(rows=rows));rows[0]['N2']=rows[0]['n']+1;rows[0]['ME']+=1
        atomic_json(new,dict(rows=rows))
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main([str(old),str(new)]),1)

    def test_offline_migration_crash_retry_and_exact_legacy_outputs(self):
        import contextlib,io,subprocess,types
        from tools.migrate_guifan import migrate
        from tools.check_guifan import main
        # Execute the repository's original producer on exactly the same pairs.
        # New appended counters may not perturb any existing floating-point bit.
        source=subprocess.check_output(['git','show','44e7726:station_score.py'],text=True)
        legacy=types.ModuleType('guifan_legacy_station');legacy.__file__=str(Path(st.__file__))
        exec(compile(source,legacy.__file__,'exec'),legacy.__dict__)
        old=self.root/'old';old.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):legacy.aggregates(self.db,old,self.end)
        with sqlite3.connect(old/'pairs.sqlite') as target:self.db.backup(target)
        original=(self.root/'current.json').read_bytes()
        with patch.object(st,'export_days',side_effect=RuntimeError('interrupted before publication')),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):migrate(self.root)
        self.assertEqual((self.root/'current.json').read_bytes(),original)
        with contextlib.redirect_stdout(io.StringIO()):
            generation=migrate(self.root)
            self.assertEqual(main([str(old),str(self.root/'snapshots'/generation),'--sample','10']),0)
        self.assertFalse(any(p.name.startswith('.incomplete-') for p in (self.root/'snapshots').iterdir()))


class CacheAndPreview(unittest.TestCase):
    def openmeteo(self,root):
        """Load the in-repo scorer with offline data-adapter fakes (WSL symlinks target Spark)."""
        import importlib.util
        import sys
        from types import SimpleNamespace
        from datetime import timezone
        ec=SimpleNamespace(ROOT=root,UTC=UTC,BJT=timezone(timedelta(hours=8)),MODEL='ECMWF-IFS-9km-OpenMeteo',
                           iso=iso,stamp=lambda s:datetime.fromisoformat(s.replace('Z','+00:00')),atomic_json=atomic_json)
        spec=importlib.util.spec_from_file_location('guifan_openmeteo_test',Path(__file__).with_name('openmeteo_verification.py'))
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,openmeteo_ecmwf=ec,openmeteo_regional=SimpleNamespace()):spec.loader.exec_module(module)
        return module,ec

    def test_openmeteo_bins_and_pruned_memo_survive_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);m,ec=self.openmeteo(root)
            now=datetime.now(UTC).replace(microsecond=0);valid=now-timedelta(hours=2);run=valid-timedelta(hours=24)
            row=dict(dataset='openmeteo-降水-24',n=1,H=1,M=0,F=0,threshold=3,valid=m.bjt(valid),run=m.bjt(run),lead=24,model=ec.MODEL,created=1)
            atomic_json(root/'score-memo.json',dict(revision='openmeteo-verification-v2',entries={'pruned':dict(signature=['old'],rows=[row])}))
            ec.runs=lambda:[]
            with patch.object(m,'city_grid',return_value=(pd.DataFrame({'lon':[114.],'lat':[22.]}),1)),patch.object(m,'signature',return_value=[]):
                value=m.refresh()
            self.assertEqual(value['rows'],[row])
            self.assertEqual(value['datasets'][0]['thresholds'],[.1,3,10,25,50,100,250])
            self.assertTrue(value['datasets'][0]['has_trace'])
            self.assertEqual(len(value['datasets'][0]['grades']),6)
            self.assertEqual(m.catalog()['rows'],[row])

    def test_openmeteo_recomputes_counts_and_keeps_legacy_thresholds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);m,ec=self.openmeteo(root)
            end=datetime(2026,10,25,12,tzinfo=UTC);run=end-timedelta(hours=26)
            valid=run+timedelta(hours=24)
            old=dict(dataset='openmeteo-降水-24',n=4,H=2,M=1,F=1,threshold=3,valid=m.bjt(valid),run=m.bjt(run),lead=24,model=ec.MODEL,created=1)
            atomic_json(root/'score-memo.json',dict(revision='openmeteo-verification-v2',entries={'r:24:city:24':dict(signature=[],rows=[old])}))
            ec.RAIN_WINDOWS=(3,24);ec.runs=lambda:[dict(id='r')];ec.run_time=lambda _:run
            ec.read_manifest=lambda _:dict(segments={'part':dict(sha256='x',retrieved_at='x')})
            ec.available_leads=lambda _:[24];ec.native_step=lambda _:1
            ec.native_field=lambda *a:(None,np.array([1.,2.,2.0000001,3.]))
            ec.rain=lambda *a:(None,np.array([.1,0.,3.,250.]))
            ec.interpolate=lambda p,f,t,nearest:f
            with patch.dict('os.environ',GUIFAN_SCORE_END=iso(end)), \
                 patch.object(m,'city_grid',return_value=(pd.DataFrame({'lon':[114.]*4,'lat':[22.]*4}),4)), \
                 patch.object(m,'signature',return_value=[]),patch.object(m,'observation_inputs',return_value=[root/'dummy']), \
                 patch.object(m,'observed_values',side_effect=lambda p,g,t,h:np.zeros(4) if not h else np.array([0.,.05,4.,250.])):
                result=m.refresh()
            temp=next(r for r in result['rows'] if r['dataset']=='openmeteo-气温-2m')
            self.assertEqual((temp['N1'],temp['N2']),(1,3))
            rain=[r for r in result['rows'] if r['dataset']=='openmeteo-降水-24']
            self.assertEqual(sorted(r['threshold'] for r in rain if not r.get('grade')),[.1,3,10,25,50,100,250])
            self.assertEqual(len([r for r in rain if r.get('grade')]),6)
            for r in rain:
                if r.get('threshold') is not None:
                    t=r['threshold'];fc=np.array([.1,0,3,250]);ob=np.array([0,.05,4,250]);old_score=st.contingency(fc,ob,t)
                    self.assertEqual([r[k] for k in ('H','M','F','n')],[old_score[k] for k in ('H','M','F','n')])

    def test_overlay_never_collapses_grade_rows(self):
        import spark_overlay
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'base.json'
            atomic_json(path,dict(datasets=[dict(id='rain',models=['m'])],rows=[]))
            base=dict(dataset='rain',model='m',run='r',valid='v',lead=3,threshold=None)
            rows=[dict(base,grade=k,H=k,M=0,F=0,n=100) for k in range(1,7)]
            merged=spark_overlay.overlay(dict(datasets=[dict(id='rain',models=['m'])],rows=rows),path)
            self.assertEqual(merged['rows'],rows)

    def test_native_pruned_rows_survive_signature_bump(self):
        import native_archive as n
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'data').mkdir()
            (root/'data/shenzhen_streets.geojson').write_text(json.dumps(dict(features=[dict(geometry=dict(type='Polygon',coordinates=[[[113,22],[115,22],[115,24],[113,24],[113,22]]]))])))
            (root/'data/grid.xlsx').touch()
            valid=datetime.now(UTC)-timedelta(hours=2)
            row=dict(dataset='hk-气温-2m',model='ECMWF-IFS',valid=valid.astimezone(n.BJT).isoformat()[:19],run=valid.astimezone(n.BJT).isoformat()[:19],lead=0,threshold=None,n=1,ME=1.,MAE=1.,RMSE=1.,CC=None,created=1)
            wn3=dict(row,model=n.WN3_MODEL)
            cache=root/'scores.json';atomic_json(cache,dict(version='hk-native-025-v1',files={'pruned':dict(signature=[],rows=[row]),n.WN3_SIGNATURE+':pruned':dict(signature=dict(adapter=n.WN3_SIGNATURE),rows=[wn3])}))
            frame=pd.DataFrame(dict(GRIDID=[1],west=[114.],east=[114.1],south=[22.5],north=[22.6]))
            with patch.object(n.pd,'read_excel',return_value=frame),patch.dict('os.environ',NATIVE_AUX_ROOT=str(root)):
                datasets,rows,issues=n.build_native(root,root/'absent-weather',cache)
            self.assertEqual(rows,[row,wn3]);self.assertTrue(datasets[0]['has_acc'])


if __name__=='__main__':unittest.main()
